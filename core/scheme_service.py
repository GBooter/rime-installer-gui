"""方案装卸服务：aria2 多镜像下载 codeload tarball → 预置 plum/package/ → rime-install。

加速路径（已核实 plum 机制，见 resources/plum/scripts/install-packages.sh）：
- package_dir = <plum>/package/<user>/<repo 去 rime- 前缀>（见 PackageRef.pkg_dir）
- 目录已存在时只跑 update-package.sh：非 git 目录仅打印警告后正常退出，不影响安装
→ 先把 tarball 解压预置到该目录，再跑 rime-install，全程绕过 GitHub git clone。

下载用内置 aria2-next（rayburst 同款内核），多镜像（直连 + gh-proxy/ghfast）
在单步 bash 脚本内按序回退，进度经 aria2 的 (NN%) 输出实时解析。
"""

from __future__ import annotations

import hashlib
import shlex
import shutil
from pathlib import Path

import config
from core.manifest import tree_hash
from core.runner import PERCENT_RE, Step, shell_wrap
from core.schemes import PackageRef, Scheme

# codeload 直连 + 国内镜像前缀（拼在完整 URL 前）
MIRROR_PREFIXES = ("https://gh-proxy.com/", "https://ghfast.top/")

#: 分支未指定时的探测顺序（v1.0.20）：HEAD 由 GitHub 解析为仓库默认分支，
#: 后面两条兜底 master/main 两种历史命名。实测 thep0y/fcitx5-themes-candlelight
#: 默认分支为 main，写死 master 时三镜像全 404。
_AUTO_REF_PATHS = ("HEAD", "refs/heads/main", "refs/heads/master")


def _ref_paths(ref: PackageRef) -> tuple[str, ...]:
    if ref.branch:
        return (f"refs/heads/{ref.branch}",)
    return _AUTO_REF_PATHS


def tarball_urls(ref: PackageRef) -> list[str]:
    """下载源列表：首选 ref 走「直连 + 两个镜像」，兜底分支只加直连。

    镜像只拼在首选 ref 上（rime-ice 这类 30MB 包在国内必须走镜像）；
    兜底分支只是「仓库名/分支名猜错」时的救济，保持 5 条以内是刻意的——
    否则一次失败刷出 9 条日志，把用户真正的错误埋在噪音里。
    """
    paths = _ref_paths(ref)
    primary = f"https://codeload.github.com/{ref.user}/{ref.repo}/tar.gz/{paths[0]}"
    urls = [primary, *(prefix + primary for prefix in MIRROR_PREFIXES)]
    urls.extend(
        f"https://codeload.github.com/{ref.user}/{ref.repo}/tar.gz/{path}"
        for path in paths[1:]
    )
    return urls


def aria2_program() -> str:
    binary = config.aria2_binary()
    if binary is None or not Path(binary).exists():
        raise RuntimeError("未找到当前架构的 aria2 二进制（resources/bin/）")
    return str(binary)


def _aria2_flags() -> list[str]:
    """显式采用 rayburst aria2.conf 的等效参数，避免依赖用户级 ~/.aria2/aria2.conf。"""
    return [
        "--no-conf",
        "-x", "16",
        "-s", "16",
        "--max-tries=0",
        "--retry-wait=2",
        "--connect-timeout=10",
        "--timeout=10",
        "--file-allocation=none",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--console-log-level=notice",
        "--summary-interval=0",
    ]


def build_download_step(ref: PackageRef, work_dir: Path) -> Step:
    """单步：bash 循环依次尝试各镜像；任一成功即退出，全部失败返回非 0。"""
    aria2 = aria2_program()
    tarball = work_dir / f"{ref.user}-{ref.repo}.tar.gz"
    urls = tarball_urls(ref)
    script = "\n".join(
        [
            "set -u",
            "for url in " + " ".join(shlex.quote(u) for u in urls) + "; do",
            '  echo "尝试下载源：$url"',
            f"  if {shlex.quote(aria2)} "
            + " ".join(shlex.quote(f) for f in _aria2_flags())
            + f" --dir={shlex.quote(str(work_dir))}"
            + f" --out={shlex.quote(tarball.name)}"
            + ' "$url"; then',
            "    exit 0",
            "  fi",
            '  echo "该下载源失败，切换下一个镜像"',
            "done",
            "exit 1",
        ]
    )
    return Step(
        f"下载 {ref.label}（aria2 加速 · 多镜像回退）",
        ["bash", "-c", script],
        weight=2,
        progress_re=PERCENT_RE,
        work_dir=str(work_dir),
    )


def build_extract_step(ref: PackageRef, work_dir: Path, dest: Path) -> Step:
    # -m（--touch）：不恢复 tarball 内的 mtime——解压目标可能是只读挂载/
    # 受限环境（utime 报 EPERM 直接让 tar 退 2，v1.0.14 冒烟与 v1.0.13 deb
    # 构建实证）；rime 数据文件的时间戳无意义，部署走显式 --build。
    tarball = work_dir / f"{ref.user}-{ref.repo}.tar.gz"
    script = (
        f"mkdir -p {shlex.quote(str(dest))} && "
        f"tar -xzf {shlex.quote(str(tarball))} "
        f"-C {shlex.quote(str(dest))} --strip-components=1 -m"
    )
    return Step(
        f"解压 {ref.label}",
        ["bash", "-c", script],
        weight=1,
        work_dir=str(work_dir),
    )


#: plum install_files_from_package() 的收集范围（根目录 *.yaml / *.txt / *.gram + opencc/*）
_RIME_DATA_PATTERNS = ("*.yaml", "*.txt", "*.gram")


def build_verify_step(scheme: Scheme) -> Step:
    """解压后校验包内容：根目录必须有 RIME 数据文件（v1.0.20）。

    plum 的 install-packages.sh 在无 recipe 时只收集根目录 *.yaml/*.txt/*.gram
    与 opencc/*；一个文件都收不到时它打印 `No files updated.` 并 **exit 0**。
    也就是说主题包、图片仓库会以「安装成功」收场，而 RIME 里什么都没多——
    假成功比失败更难排查，故在 rime-install 之前把这种情况变成明确错误。
    """
    lines = ["set -u", "bad=0"]
    for ref in scheme.packages:
        pkg = config.PLUM_DIR / "package" / ref.pkg_dir
        q = shlex.quote(str(pkg))
        cond = " || ".join(
            f"ls {q}/{pattern} >/dev/null 2>&1" for pattern in _RIME_DATA_PATTERNS
        )
        cond += f" || [ -d {q}/opencc ]"
        lines += [
            f"if {cond}; then",
            f'  echo "包内容校验通过：{ref.label}"',
            "else",
            f'  echo "!! {ref.label} 里没有找到可安装的 RIME 数据文件'
            '（*.yaml / *.txt / *.gram / recipe.yaml）"',
            '  echo "!! 已终止，未改动你的 RIME 配置——这类仓库多为皮肤主题、'
            '词典源码或普通软件项目，不是输入方案"',
            '  echo "!! 建议：① 装皮肤/主题 →「外观」页（用户主题目录 + 刷新主题目录）；'
            '② 装方案 → 确认仓库根目录直接有 default.yaml 之类的配置文件"',
            "  bad=1",
            "fi",
        ]
    lines.append("exit $bad")
    return Step(
        "校验包内容（确认真是 RIME 方案包）",
        ["bash", "-c", "\n".join(lines)],
        weight=1,
        hint_key="msg.not_rime_package",
    )


def build_install_step(scheme: Scheme, frontend: str, rime_dir: Path) -> Step:
    """rime-install 步骤：显式传 rime_dir / rime_frontend，杜绝装错目录（Deepin 必须）。"""
    env_program = shutil.which("env") or "/usr/bin/env"
    argv = [
        env_program,
        f"rime_dir={rime_dir}",
        f"rime_frontend={frontend}",
        str(config.PLUM_RIME_INSTALL),
        *scheme.targets,
    ]
    return Step(
        f"安装方案 {scheme.name}（东风破 rime-install）",
        shell_wrap(argv),
        weight=2,
        work_dir=str(config.PLUM_DIR),
    )


def clear_package_cache(scheme: Scheme) -> list[Path]:
    """删除 plum/package/ 下该方案的缓存目录。

    必须清理：plum 发现 package_dir 已存在时会跳过拉取、直接安装旧内容，
    导致「更新」形同无效。仅在 PLUM_DIR 内操作，路径由 PackageRef 构造。
    """
    removed: list[Path] = []
    root = (config.PLUM_DIR / "package").resolve()
    for ref in scheme.packages:
        target = (config.PLUM_DIR / "package" / ref.pkg_dir).resolve()
        if not str(target).startswith(str(root)):  # 越界防护
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            if not target.exists():
                removed.append(target)
    return removed


def build_clean_step(scheme: Scheme) -> Step:
    """安装/重装前的缓存清理步骤（保证 aria2 拉到的是最新内容）。"""
    targets = [str(config.PLUM_DIR / "package" / ref.pkg_dir) for ref in scheme.packages]
    script = "rm -rf " + " ".join(shlex.quote(t) for t in targets)
    return Step(
        "清理本地包缓存（确保获取最新内容）",
        ["bash", "-c", script],
        weight=1,
    )


def build_install_steps(
    scheme: Scheme,
    frontend: str,
    rime_dir: Path,
    work_dir: Path,
    clean: bool = True,
) -> list[Step]:
    """完整安装计划：清缓存 → 每包下载 → 解压预置 → 校验包内容 → 统一 rime-install。"""
    steps: list[Step] = []
    if clean:
        steps.append(build_clean_step(scheme))
    for ref in scheme.packages:
        steps.append(build_download_step(ref, work_dir))
    for ref in scheme.packages:
        dest = config.PLUM_DIR / "package" / ref.pkg_dir
        steps.append(build_extract_step(ref, work_dir, dest))
    if scheme.packages:
        steps.append(build_verify_step(scheme))
    steps.append(build_install_step(scheme, frontend, rime_dir))
    return steps


def build_check_steps(scheme: Scheme, work_dir: Path) -> list[Step]:
    """更新检查计划：下载 + 解压到临时目录（不安装），供指纹对比。"""
    steps: list[Step] = []
    for ref in scheme.packages:
        steps.append(build_download_step(ref, work_dir))
    for ref in scheme.packages:
        dest = work_dir / ref.pkg_dir
        steps.append(build_extract_step(ref, work_dir, dest))
    return steps


def package_tree_hash(base_dir: Path, scheme: Scheme) -> str:
    """方案的「上游内容指纹」：各包解压树哈希的组合，用于更新检查。"""
    digest = hashlib.sha256()
    for ref in scheme.packages:
        digest.update(f"{ref.pkg_dir}\0{tree_hash(base_dir / ref.pkg_dir)}\n".encode("utf-8"))
    return digest.hexdigest()
