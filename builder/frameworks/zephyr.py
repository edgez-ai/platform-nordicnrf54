# Copyright 2019-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
The Zephyr Project is a scalable real-time operating system (RTOS) supporting multiple
hardware architectures, optimized for resource constrained devices, and built with
safety and security in mind.

https://github.com/zephyrproject-rtos/zephyr
"""

from os.path import join
import subprocess
import os
import re
import shutil
import time

from SCons.Script import Import, SConscript

try:
    import yaml
except ImportError:
    subprocess.run(["pip", "install", "pyyaml"], check=True)
    import yaml


Import("env")

platform_name = env.subst("$PIOPLATFORM")
board_name = env.get("BOARD", "")

# Zephyr's internal install logic expects known nRF PlatformIO platform names.
if board_name and "nrf" in board_name:
    env.Replace(PIOPLATFORM="nordicnrf52")


def _is_commit_hash(value):
    return value and re.match(r"[0-9a-f]{7,}$", value) is not None


def _git_clone_with_retry(url, dst, revision, max_retries=3, retry_delay=5):
    for attempt in range(1, max_retries + 1):
        args = ["git", "clone"]
        is_commit = _is_commit_hash(revision)

        if not is_commit and revision:
            args.extend(["--branch", revision, "--depth", "1"])
        elif not is_commit:
            args.extend(["--depth", "1"])

        try:
            print("  Cloning %s (attempt %d/%d)" % (url, attempt, max_retries))
            subprocess.run(args + [url, dst], check=True, capture_output=True, text=True)
            if is_commit and revision:
                subprocess.run(
                    ["git", "-C", dst, "checkout", revision],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            print("  OK: %s" % os.path.basename(dst))
            return True
        except subprocess.CalledProcessError as exc:
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            if attempt < max_retries:
                tail = (exc.stderr or str(exc)).strip()
                print("  Failed (attempt %d): %s" % (attempt, tail))
                print("  Retrying in %ds..." % retry_delay)
                time.sleep(retry_delay)
            else:
                print("  FAILED after %d attempts: %s" % (max_retries, url))
                return False

    return False


def _preinstall_west_deps(framework_dir, platform_name_hint):
    west_yml = join(framework_dir, "west.yml")
    if not os.path.isfile(west_yml):
        return

    pio_dir = join(framework_dir, "_pio")

    with open(west_yml, "r", encoding="utf-8") as fp:
        west_data = yaml.safe_load(fp)

    manifest = west_data.get("manifest", {})
    remotes = {r["name"]: r for r in manifest.get("remotes", [])}
    default_remote = manifest.get("defaults", {}).get("remote", "")

    hal_platforms = {"nordicnrf52", "nordicnrf51"}
    if platform_name_hint not in hal_platforms:
        return

    print("Pre-installing Zephyr west dependencies (with retry)...")

    for proj in manifest.get("projects", []):
        name = proj.get("name", "")
        proj_path = proj.get("path", name)

        if proj_path.startswith("tool") or name.startswith("nrf_hw_"):
            continue

        if name.startswith("hal_") and name != "hal_nordic":
            continue

        dst = join(pio_dir, proj_path)
        if os.path.isdir(dst):
            continue

        if "url" in proj:
            proj_url = proj["url"]
            if not proj_url.startswith("http"):
                url_base = remotes.get(proj.get("remote", default_remote), {}).get("url-base", "")
                proj_url = url_base.rstrip("/") + "/" + proj_url.lstrip("/")
        else:
            url_base = remotes.get(proj.get("remote", default_remote), {}).get("url-base", "")
            repo_path = proj.get("repo-path", name)
            proj_url = url_base.rstrip("/") + "/" + repo_path + ".git"

        revision = proj.get("revision")
        print("Pre-installing: %s" % name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        _git_clone_with_retry(proj_url, dst, revision)

    print("Pre-install complete.")


framework_dir = env.PioPlatform().get_package_dir("framework-zephyr")
platform_dir = env.PioPlatform().get_dir()


def _ensure_executable(path):
    if not os.path.isfile(path):
        return

    mode = os.stat(path).st_mode
    # Ensure user/group/other execute bits are present for CMake find_program.
    wanted = mode | 0o111
    if wanted != mode:
        os.chmod(path, wanted)


def _ensure_zephyr_script_permissions(framework_root):
    _ensure_executable(join(framework_root, "scripts", "build", "gen_kobject_list.py"))


_ensure_zephyr_script_permissions(framework_dir)


def _ensure_pre0_alias(build_dir):
    zephyr_build_dir = join(build_dir, "zephyr")
    os.makedirs(zephyr_build_dir, exist_ok=True)

    src = join(zephyr_build_dir, "zephyr_pre0.elf")
    dst = join(zephyr_build_dir, "firmware-pre0.elf")

    if not os.path.isfile(src):
        return

    shutil.copyfile(src, dst)


_ensure_pre0_alias(env.subst("$BUILD_DIR"))

# Symlink platform-specific custom board definitions so Zephyr can discover them.
platform_boards_dir = join(platform_dir, "zephyr", "boards", "arm")
framework_boards_dir = join(framework_dir, "boards", "arm")

if os.path.isdir(platform_boards_dir):
    os.makedirs(framework_boards_dir, exist_ok=True)
    for board_name_dir in os.listdir(platform_boards_dir):
        src = join(platform_boards_dir, board_name_dir)
        dst = join(framework_boards_dir, board_name_dir)
        if not os.path.isdir(src):
            continue
        if os.path.exists(dst) or os.path.islink(dst):
            continue
        try:
            os.symlink(src, dst)
            print("Linked board: %s -> %s" % (board_name_dir, src))
        except OSError:
            shutil.copytree(src, dst)
            print("Copied board: %s -> %s" % (board_name_dir, dst))


_preinstall_west_deps(framework_dir, env.subst("$PIOPLATFORM"))

SConscript(join(framework_dir, "scripts", "platformio", "platformio-build.py"), exports="env")

# PlatformIO expects firmware-pre0.elf for ISR generation, while Zephyr emits
# zephyr_pre0.elf. Build the pre0 ELF and mirror it to the expected filename.
build_dir = env.subst("$BUILD_DIR")
ninja_bin = join(env.PioPlatform().get_package_dir("tool-ninja") or "", "ninja")
if os.path.isfile(ninja_bin):
    try:
        subprocess.run([ninja_bin, "-C", build_dir, "zephyr/zephyr_pre0.elf"], check=True)
    except subprocess.CalledProcessError:
        pass

_ensure_pre0_alias(build_dir)

if board_name and "nrf" in board_name:
    env.Replace(PIOPLATFORM=platform_name)