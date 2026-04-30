#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Allwinner Fastboot Flash Tool
自动化刷写全志平台固件的工具脚本
支持直接指定打包镜像文件（自动解包）或已解包的固件目录

使用方法:
    python aw_fastboot_flash.py <固件目录或镜像文件> [选项]

选项:
    -s, --secure                      安全固件模式（OEM 解锁 + toc0/toc1）
    --gsi <system.img路径>             刷入 GSI (Google System Image)
    --vts <vendor_boot-debug.img路径>  刷入 VTS vendor_boot-debug 镜像
    --output-dir <目录>                指定镜像解包输出目录（默认: ./output）

示例:
    # 直接指定打包镜像文件刷机（自动解包到 ./output）
    python aw_fastboot_flash.py D:\\firmware\\sun55iw3p1_android13_a133_p3.img

    # 指定解包输出目录
    python aw_fastboot_flash.py D:\\firmware\\sun55iw3p1.img --output-dir D:\\tmp\\extracted

    # 标准刷机（指定已解包的目录）
    python aw_fastboot_flash.py D:\\firmware\\output

    # 安全固件刷机
    python aw_fastboot_flash.py D:\\firmware\\output -s

    # 带 GSI 刷机
    python aw_fastboot_flash.py D:\\firmware\\output --gsi D:\\gsi\\system.img

    # 带 VTS 刷机
    python aw_fastboot_flash.py D:\\firmware\\output --vts D:\\vts\\vendor_boot-debug.img

    # GSI + VTS 组合刷机
    python aw_fastboot_flash.py D:\\firmware\\output --gsi D:\\gsi\\system.img --vts D:\\vts\\vendor_boot-debug.img
"""

import subprocess
import sys
import time
import argparse
import struct
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple
from enum import Enum, auto

# 刷机分区配置，按顺序执行
# 格式: (分区名, 文件名, 是否必须)
FLASH_PARTITIONS = [
    ("boot0", "boot0_sdcard.fex", True),
    ("u-boot", "boot_package.fex", True),
    ("mbr", "sunxi_mbr.fex", True),
    ("env", "env.fex", True),
    ("bootloader", "boot-resource.fex", True),
    ("boot", "boot.img", True),
    ("dtbo", "dtbo.img", False),
    ("init_boot", "init_boot.img", False),
    ("vendor_boot", "vendor_boot.img", False),
    ("misc", "misc.img", False),
    ("super", "super.img", True),
    ("vbmeta", "vbmeta.img", False),
    ("vbmeta_system", "vbmeta_system.img", False),
    ("vbmeta_vendor", "vbmeta_vendor.img", False),
]

# 安全固件分区文件替换映射（-s 模式）
# boot0: boot0_sdcard.fex -> toc0.fex
# u-boot: boot_package.fex -> toc1.fex
SECURE_PARTITION_REPLACEMENTS = {
    "boot0": "toc0.fex",
    "u-boot": "toc1.fex",
}

PartitionFile = Tuple[str, str, Path]

# 全志固件镜像解包配置
_IMG_MAGIC = b'\x00\x01\x00\x00\x00\x04\x00\x00'
_IMG_ENTRY_SIZE = 1024
_IMG_MAX_ENTRIES = 256
_IMG_COPY_BUF = 4 * 1024 * 1024  # 4MB 分块写入缓冲

# 非 RFSFAT16 类型中允许提取的文件名
_EXTRACT_KNOWN_NAMES = frozenset({
    "sunxi_mbr.fex", "toc0.fex", "toc0_ufs.fex", "toc1.fex",
    "boot0_sdcard.fex", "boot0_nand.fex", "u-boot.fex", "boot_package.fex",
})

# RFSFAT16 类型中保留 .fex 后缀不重命名为 .img 的文件
_EXTRACT_KEEP_FEX = frozenset({"env.fex", "boot-resource.fex"})

LOG_FILE: Optional[Path] = None
TARGET_SERIAL: Optional[str] = None
SERIAL_LOCKED = False


class FlashMode(Enum):
    """刷机模式"""
    FULL = auto()           # 完整刷机（所有分区）
    GSI_ONLY = auto()       # 仅刷 GSI system.img
    VTS_ONLY = auto()       # 仅刷 VTS vendor_boot-debug.img
    FULL_WITH_GSI = auto()  # 完整刷机 + GSI
    FULL_WITH_VTS = auto()  # 完整刷机 + VTS
    FULL_WITH_ALL = auto()  # 完整刷机 + GSI + VTS
    GSI_WITH_VTS = auto()   # 仅 GSI + VTS


class Colors:
    """终端颜色定义"""
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BOLD = "\033[1m"


def print_colored(message: str, color: str = Colors.RESET) -> None:
    """打印带颜色的消息"""
    print(f"{color}{message}{Colors.RESET}")
    append_log(message)


def append_log(message: str) -> None:
    """将消息追加到日志文件"""
    if LOG_FILE is None:
        return
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as fp:
            fp.write(f"[{timestamp}] {message}\n")
    except OSError:
        # 日志写入失败不应影响刷机主流程
        pass


def print_header() -> None:
    """打印脚本头部信息"""
    print_colored("=" * 60, Colors.CYAN)
    print_colored("    Allwinner Fastboot Flash Tool v2.3", Colors.BOLD + Colors.CYAN)
    print_colored("    全志平台固件自动刷写工具", Colors.CYAN)
    print_colored("    支持镜像解包 / GSI / VTS 刷入", Colors.CYAN)
    print_colored("=" * 60, Colors.CYAN)
    print()


def setup_logging() -> Optional[Path]:
    """初始化刷机日志文件"""
    global LOG_FILE

    log_dir = Path(__file__).resolve().parent / "flash_logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        LOG_FILE = log_dir / f"aw_fastboot_flash_{time.strftime('%Y%m%d_%H%M%S')}.log"
        LOG_FILE.touch(exist_ok=False)
        append_log("日志初始化完成")
        return LOG_FILE
    except OSError as e:
        print_colored(f"[!] 日志文件初始化失败，将仅输出到终端: {e}", Colors.YELLOW)
        LOG_FILE = None
        return None


def enable_windows_ansi_colors() -> None:
    """在 Windows 控制台启用 ANSI 颜色支持"""
    if sys.platform != "win32":
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        stdout_handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()

        if kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(stdout_handle, mode.value | 0x0004)
    except Exception as e:
        append_log(f"[warn] Windows ANSI 颜色初始化失败: {e}")


def build_adb_command(*args: str, serial: Optional[str] = None) -> List[str]:
    """构造 adb 命令，优先使用指定或已锁定的设备序列号"""
    command = ["adb"]
    active_serial = serial if serial is not None else TARGET_SERIAL
    if active_serial:
        command.extend(["-s", active_serial])
    command.extend(args)
    return command


def build_fastboot_command(*args: str, serial: Optional[str] = None) -> List[str]:
    """构造 fastboot 命令，优先使用指定或已锁定的设备序列号"""
    command = ["fastboot"]
    active_serial = serial if serial is not None else TARGET_SERIAL
    if active_serial:
        command.extend(["-s", active_serial])
    command.extend(args)
    return command


def run_command(args: list, timeout: int = 30) -> Tuple[bool, str]:
    """
    执行命令
    返回: (是否成功, 输出信息)
    """
    command_str = " ".join(args)
    append_log(f"$ {command_str}")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        output = result.stdout + result.stderr
        append_log(f"[ret={result.returncode}] {output.strip() or '<无输出>'}")
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        append_log(f"[timeout] 命令执行超时（{timeout}s）")
        return False, "命令执行超时"
    except FileNotFoundError:
        append_log(f"[error] 未找到命令: {args[0]}")
        return False, f"未找到命令: {args[0]}"
    except Exception as e:
        append_log(f"[error] {e}")
        return False, str(e)


def clear_directory_contents(directory: Path) -> None:
    """清空目录内容，保留目录本身"""
    for entry in directory.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def remove_directory_quietly(directory: Path) -> None:
    """尽量清理临时目录，不抛出异常"""
    try:
        shutil.rmtree(directory)
    except OSError:
        pass


def read_img_u32(fp, offset: int, field_name: str, entry_index: int) -> int:
    """从镜像条目中读取 32 位无符号整数"""
    fp.seek(offset)
    raw = fp.read(4)
    if len(raw) != 4:
        raise ValueError(f"镜像条目 #{entry_index} 的 {field_name} 字段不完整")
    return struct.unpack('<I', raw)[0]


def validate_extract_name(name: str, entry_index: int) -> str:
    """校验镜像条目文件名，避免覆盖意外路径"""
    normalized = name.strip()
    if not normalized:
        raise ValueError(f"镜像条目 #{entry_index} 的文件名为空")
    if Path(normalized).name != normalized:
        raise ValueError(f"镜像条目 #{entry_index} 的文件名非法: {name!r}")
    return normalized


def normalize_optional_image_path(
    path: Optional[Path],
    label: str,
    expected_filename: str,
) -> Optional[Path]:
    """规范化可选镜像路径；若传入目录则尝试定位默认文件名"""
    if path is None:
        return None

    if not path.exists():
        raise FileNotFoundError(f"{label} 文件不存在: {path}")

    if path.is_dir():
        candidate = path / expected_filename
        if candidate.is_file():
            print_colored(f"[*] {label} 路径是目录，已自动使用: {candidate}", Colors.CYAN)
            return candidate
        raise ValueError(f"{label} 路径是目录，但未找到 {expected_filename}: {path}")

    if not path.is_file():
        raise ValueError(f"{label} 路径不是普通文件: {path}")

    return path


def commit_unpacked_output(staging_dir: Path, output_dir: Path) -> bool:
    """将临时解包目录原子化提交到目标输出目录"""
    try:
        if output_dir.exists():
            if not output_dir.is_dir():
                print_colored(f"    [✗] 输出路径不是目录: {output_dir}", Colors.RED)
                return False

            existing_entries = list(output_dir.iterdir())
            if existing_entries:
                print_colored(f"    [!] 输出目录非空: {output_dir}", Colors.YELLOW)
                if not confirm_action("是否删除现有内容并写入新的解包结果?"):
                    print_colored("    [!] 已取消覆盖输出目录，原有内容保持不变", Colors.YELLOW)
                    return False
                clear_directory_contents(output_dir)

            for item in staging_dir.iterdir():
                shutil.move(str(item), str(output_dir / item.name))
            remove_directory_quietly(staging_dir)
            return True

        staging_dir.rename(output_dir)
        return True
    except OSError as e:
        print_colored(f"    [✗] 写入输出目录失败: {e}", Colors.RED)
        append_log(f"[error] 解包结果写入失败: {e}")
        return False


def list_adb_devices() -> List[str]:
    """获取当前 adb 设备列表"""
    success, output = run_command(["adb", "devices"], timeout=5)
    if not success:
        return []

    devices = []
    for line in output.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ["device", "recovery", "sideload"]:
            devices.append(parts[0])
    return devices


def list_fastboot_devices() -> List[str]:
    """获取当前 fastboot 设备列表"""
    success, output = run_command(["fastboot", "devices"], timeout=5)
    if not success:
        return []

    devices = []
    for line in output.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lower().startswith("fastboot"):
            devices.append(parts[0])
        elif len(parts) == 1:
            token = parts[0].strip()
            if token and "waiting" not in token.lower():
                devices.append(token)
                append_log(f"[warn] fastboot 输出缺少状态列，按设备序列号处理: {line}")
            else:
                append_log(f"[warn] 已忽略无法确认的 fastboot 输出: {line}")
    return devices


def select_active_serial(candidates: List[str], announce_change: bool = False) -> Optional[str]:
    """从候选设备中选择当前应操作的序列号"""
    global TARGET_SERIAL

    unique_candidates = list(dict.fromkeys(candidates))
    if not unique_candidates:
        return None

    if TARGET_SERIAL and TARGET_SERIAL in unique_candidates:
        return TARGET_SERIAL

    if SERIAL_LOCKED:
        return None

    if len(unique_candidates) == 1:
        new_serial = unique_candidates[0]
        if TARGET_SERIAL and TARGET_SERIAL != new_serial and announce_change:
            print_colored(
                f"[!] 检测到设备序列号从 {TARGET_SERIAL} 切换为 {new_serial}，当前仅连接一台设备，已自动跟随",
                Colors.YELLOW
            )
        TARGET_SERIAL = new_serial
        return TARGET_SERIAL

    return None


def resolve_target_serial(preferred_serial: Optional[str] = None) -> bool:
    """锁定本次刷机的目标设备序列号"""
    global TARGET_SERIAL, SERIAL_LOCKED

    adb_devices = list_adb_devices()
    fastboot_devices = list_fastboot_devices()
    unique_devices = list(dict.fromkeys(adb_devices + fastboot_devices))

    if preferred_serial:
        TARGET_SERIAL = preferred_serial
        SERIAL_LOCKED = True
        if preferred_serial not in unique_devices:
            print_colored(f"[✗] 未检测到指定设备序列号: {preferred_serial}", Colors.RED)
            print_colored("    请确认设备已连接，或检查 --serial 参数是否正确", Colors.YELLOW)
            return False
        print_colored(f"[✓] 已锁定目标设备序列号: {TARGET_SERIAL}", Colors.GREEN)
        return True

    if not unique_devices:
        print_colored("[✗] 未检测到任何 adb/fastboot 设备", Colors.RED)
        print_colored("    请连接设备后重试", Colors.YELLOW)
        return False

    if len(unique_devices) > 1:
        print_colored("[✗] 检测到多台设备，必须通过 --serial 指定目标设备", Colors.RED)
        print_colored(f"    adb 设备: {adb_devices or ['<无>']}", Colors.YELLOW)
        print_colored(f"    fastboot 设备: {fastboot_devices or ['<无>']}", Colors.YELLOW)
        return False

    TARGET_SERIAL = unique_devices[0]
    SERIAL_LOCKED = True
    print_colored(f"[✓] 自动锁定目标设备序列号: {TARGET_SERIAL}", Colors.GREEN)
    return True


def get_fastboot_userspace(serial: Optional[str] = None) -> Optional[bool]:
    """查询 fastboot 当前是否为 userspace fastboot（fastbootd）"""
    success, output = run_command(build_fastboot_command("getvar", "is-userspace", serial=serial), timeout=10)
    output_lower = output.lower()

    if "is-userspace: yes" in output_lower or "is-userspace yes" in output_lower:
        return True
    if "is-userspace: no" in output_lower or "is-userspace no" in output_lower:
        return False

    if success:
        append_log("[warn] fastboot getvar is-userspace 未返回可识别结果")
    else:
        append_log(f"[warn] fastboot getvar is-userspace 查询失败: {output}")
    return None


def unpack_aw_img(img_path: Path, output_dir: Path) -> List[str]:
    """
    解包全志固件镜像文件，提取分区文件到指定目录
    镜像格式: 256 个条目表，每条目 1024 字节，包含 magic/类型/文件名/偏移/大小
    返回: 提取的文件名列表
    """
    print_colored(f"\n[*] 正在解包固件镜像: {img_path.name}", Colors.BLUE)
    print_colored(f"    输出目录: {output_dir}", Colors.CYAN)

    output_parent = output_dir.parent
    try:
        output_parent.mkdir(parents=True, exist_ok=True)
        img_size = img_path.stat().st_size
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}_unpack_", dir=str(output_parent)))
    except OSError as e:
        print_colored(f"    [✗] 解包准备失败: {e}", Colors.RED)
        append_log(f"[error] 解包准备失败: {e}")
        return []

    extracted: List[str] = []

    try:
        with open(img_path, 'rb') as f:
            for i in range(_IMG_MAX_ENTRIES):
                entry_offset = i * _IMG_ENTRY_SIZE
                f.seek(entry_offset)
                magic = f.read(8)
                if len(magic) < 8:
                    break
                if magic != _IMG_MAGIC:
                    continue

                main_type_raw = f.read(8)
                if len(main_type_raw) != 8:
                    raise ValueError(f"镜像条目 #{i + 1} 的 main_type 字段不完整")
                main_type = main_type_raw.decode('ascii', errors='replace').rstrip('\x00').strip()

                f.read(16)  # sub type，解包逻辑不使用

                f.seek(entry_offset + 36)
                name = f.read(32).decode('ascii', errors='replace').rstrip('\x00')

                size = read_img_u32(f, entry_offset + 300, "size", i + 1)
                data_offset = read_img_u32(f, entry_offset + 308, "data_offset", i + 1)

                # 根据类型决定是否提取及输出文件名
                out_name = name
                should_extract = False

                if main_type == "RFSFAT16":
                    if size > 4:
                        should_extract = True
                        if name not in _EXTRACT_KEEP_FEX and name.endswith('.fex'):
                            out_name = name[:-4] + '.img'
                elif name in _EXTRACT_KNOWN_NAMES:
                    should_extract = True

                if not should_extract:
                    continue

                out_name = validate_extract_name(out_name, i + 1)
                if out_name in extracted:
                    raise ValueError(f"镜像条目 #{i + 1} 的输出文件名重复: {out_name}")
                if size == 0:
                    raise ValueError(f"镜像条目 #{i + 1} 的文件大小为 0: {out_name}")

                data_end = data_offset + size
                if data_offset >= img_size or data_end > img_size:
                    raise ValueError(
                        f"镜像条目 #{i + 1} 超出镜像边界: {out_name} "
                        f"(offset={data_offset}, size={size}, img_size={img_size})"
                    )

                # 分块写入，避免大文件一次性加载到内存
                out_path = staging_dir / out_name
                f.seek(data_offset)
                remaining = size
                written = 0
                with open(out_path, 'wb') as out_f:
                    while remaining > 0:
                        chunk = f.read(min(_IMG_COPY_BUF, remaining))
                        if not chunk:
                            raise ValueError(f"镜像条目 #{i + 1} 数据截断: {out_name}")
                        out_f.write(chunk)
                        chunk_size = len(chunk)
                        written += chunk_size
                        remaining -= chunk_size

                if written != size:
                    raise ValueError(f"镜像条目 #{i + 1} 写入大小异常: {out_name}")

                size_mb = size / (1024 * 1024)
                print_colored(f"    [✓] {out_name:<25} ({size_mb:.2f} MB)", Colors.GREEN)
                extracted.append(out_name)

        if not extracted:
            print_colored("    [✗] 未从镜像中提取到任何文件", Colors.RED)
            return []

        if not commit_unpacked_output(staging_dir, output_dir):
            return []

        print_colored(f"\n[✓] 解包完成，共提取 {len(extracted)} 个文件", Colors.GREEN)
        return extracted
    except (OSError, ValueError) as e:
        print_colored(f"    [✗] 解包失败: {e}", Colors.RED)
        append_log(f"[error] 解包失败: {e}")
        return []
    finally:
        if staging_dir.exists():
            remove_directory_quietly(staging_dir)


def get_flash_partitions(secure: bool = False) -> list:
    """获取刷机分区配置，安全固件模式下替换 boot0/u-boot 对应的文件"""
    if not secure:
        return FLASH_PARTITIONS

    partitions = []
    for partition, filename, required in FLASH_PARTITIONS:
        if partition in SECURE_PARTITION_REPLACEMENTS:
            partitions.append((partition, SECURE_PARTITION_REPLACEMENTS[partition], required))
        else:
            partitions.append((partition, filename, required))
    return partitions


def oem_unlock() -> bool:
    """执行 fastboot oem unlock（安全固件刷写前需要解锁）"""
    print_colored("\n[*] 正在执行 OEM 解锁 (fastboot oem unlock)...", Colors.BLUE)
    print_colored("    如果设备端弹出解锁确认，请在设备上完成确认并等待命令返回", Colors.YELLOW)
    success, output = run_command(build_fastboot_command("oem", "unlock"), timeout=180)
    if success:
        print_colored("    [✓] OEM 解锁成功", Colors.GREEN)
        return True
    # 部分设备已解锁时会返回失败但实际可继续
    if "already unlocked" in output.lower():
        print_colored("    [✓] 设备已处于解锁状态", Colors.GREEN)
        return True
    print_colored(f"    [✗] OEM 解锁失败: {output}", Colors.RED)
    return False


def recover_after_oem_unlock() -> bool:
    """OEM 解锁后重新等待设备回稳并拉回 bootloader"""
    print_colored("\n[*] 正在等待 OEM 解锁后的设备状态稳定...", Colors.BLUE)

    if wait_for_fastboot_any(timeout=120):
        return reboot_to_bootloader()

    state = get_device_state()
    if state == "adb":
        print_colored("    [!] 设备已回到 adb，重新进入 bootloader...", Colors.YELLOW)
        return reboot_to_bootloader()

    print_colored("[✗] OEM 解锁后未能重新检测到目标设备", Colors.RED)
    print_colored("    请确认设备端是否完成了解锁确认，并检查 USB 连接", Colors.YELLOW)
    return False


def check_adb() -> bool:
    """检查 adb 是否可用"""
    success, _ = run_command(["adb", "version"])
    if success:
        print_colored("[✓] adb 工具已就绪", Colors.GREEN)
        return True
    print_colored("[✗] 错误: 未找到 adb 命令", Colors.RED)
    print_colored("    请确保 Android SDK Platform Tools 已安装并添加到 PATH 环境变量", Colors.YELLOW)
    return False


def check_fastboot() -> bool:
    """检查 fastboot 是否可用"""
    success, _ = run_command(["fastboot", "--version"])
    if success:
        print_colored("[✓] fastboot 工具已就绪", Colors.GREEN)
        return True
    print_colored("[✗] 错误: 未找到 fastboot 命令", Colors.RED)
    print_colored("    请确保 Android SDK Platform Tools 已安装并添加到 PATH 环境变量", Colors.YELLOW)
    return False


def get_device_state(debug: bool = False) -> str:
    """
    获取设备状态
    返回: 'adb', 'fastboot', 'fastbootd', 'fastboot_unknown', 'none'
    """
    fastboot_devices = list_fastboot_devices()
    fastboot_serial = select_active_serial(fastboot_devices, announce_change=True)
    if fastboot_serial:
        userspace = get_fastboot_userspace(fastboot_serial)
        if debug:
            print_colored(
                f"    [DEBUG] fastboot 设备: {fastboot_serial}, is-userspace={userspace}",
                Colors.MAGENTA
            )
        if userspace is True:
            return 'fastbootd'
        if userspace is False:
            return 'fastboot'
        append_log(f"[warn] 已检测到 fastboot 设备 {fastboot_serial}，但无法确认是否为 fastbootd")
        return 'fastboot_unknown'

    adb_devices = list_adb_devices()
    adb_serial = select_active_serial(adb_devices, announce_change=True)
    if adb_serial:
        if debug:
            print_colored(f"    [DEBUG] adb 设备: {adb_serial}", Colors.MAGENTA)
        return 'adb'

    return 'none'


def wait_for_device(target_state: str, timeout: int = 60, accept_unknown_fastboot: bool = False) -> bool:
    """
    等待设备进入指定状态
    target_state: 'adb', 'fastboot', 'fastbootd'
    """
    target_label = "fastboot/fastbootd" if target_state == "fastboot_any" else target_state
    print_colored(f"[*] 等待设备进入 {target_label} 模式...", Colors.BLUE)
    start_time = time.time()
    check_interval = 2  # 每 2 秒检查一次
    
    while time.time() - start_time < timeout:
        state = get_device_state()
        elapsed = int(time.time() - start_time)
        
        if target_state == "fastboot_any" and state in ['fastboot', 'fastbootd', 'fastboot_unknown']:
            print_colored(f"[✓] 设备已进入 {state} 模式", Colors.GREEN)
            return True

        if state == target_state:
            print_colored(f"[✓] 设备已进入 {target_state} 模式", Colors.GREEN)
            return True

        if target_state == "fastboot" and accept_unknown_fastboot and state == "fastboot_unknown":
            print_colored("[✓] 已检测到 fastboot 设备，但 userspace 状态未知，按 bootloader fastboot 继续", Colors.YELLOW)
            return True
        
        # 显示等待进度
        if elapsed > 0 and elapsed % 10 == 0:
            print_colored(f"    ... 已等待 {elapsed}s，当前状态: {state}", Colors.YELLOW)
        
        time.sleep(check_interval)
    
    # 最后再检查一次
    state = get_device_state()
    if target_state == "fastboot_any" and state in ['fastboot', 'fastbootd', 'fastboot_unknown']:
        print_colored(f"[✓] 设备已进入 {state} 模式", Colors.GREEN)
        return True

    if state == target_state:
        print_colored(f"[✓] 设备已进入 {target_state} 模式", Colors.GREEN)
        return True

    if target_state == "fastboot" and accept_unknown_fastboot and state == "fastboot_unknown":
        print_colored("[✓] 已检测到 fastboot 设备，但 userspace 状态未知，按 bootloader fastboot 继续", Colors.YELLOW)
        return True
    
    print_colored(f"[✗] 等待超时 ({timeout}s)，设备未进入 {target_label} 模式", Colors.RED)
    print_colored(f"    当前检测到的状态: {state}", Colors.YELLOW)
    print_colored("    提示: 请手动检查 'fastboot devices' 命令输出", Colors.YELLOW)
    return False


def reboot_to_bootloader() -> bool:
    """通过 adb 重启到 bootloader 模式"""
    state = get_device_state()
    
    if state == 'fastboot':
        print_colored("[✓] 设备已在 fastboot 模式", Colors.GREEN)
        return True
    
    if state == 'fastbootd':
        print_colored("[*] 设备在 fastbootd 模式，切换到 bootloader...", Colors.BLUE)
        success, output = run_command(build_fastboot_command("reboot", "bootloader"), timeout=30)
        if not success:
            print_colored(f"[✗] fastboot reboot bootloader 失败: {output}", Colors.RED)
            return False
        return wait_for_device('fastboot', timeout=30, accept_unknown_fastboot=True)

    if state == 'fastboot_unknown':
        print_colored("[!] 已检测到 fastboot 设备，但无法确认是否处于 bootloader fastboot，尝试显式切回 bootloader...", Colors.YELLOW)
        success, output = run_command(build_fastboot_command("reboot", "bootloader"), timeout=30)
        if not success:
            print_colored(f"[✗] fastboot reboot bootloader 失败: {output}", Colors.RED)
            return False
        return wait_for_device('fastboot', timeout=30, accept_unknown_fastboot=True)
    
    if state == 'adb':
        print_colored("[*] 通过 adb 重启到 bootloader...", Colors.BLUE)
        success, output = run_command(build_adb_command("reboot", "bootloader"))
        if not success:
            print_colored(f"[✗] adb reboot bootloader 失败: {output}", Colors.RED)
            return False
        return wait_for_device('fastboot', timeout=60, accept_unknown_fastboot=True)
    
    print_colored("[✗] 未检测到任何设备", Colors.RED)
    print_colored("    请确保设备已连接并启用 USB 调试", Colors.YELLOW)
    return False


def reboot_to_fastbootd() -> bool:
    """
    重启到 fastbootd 模式（用于 GSI 刷写）
    通过 fastboot getvar is-userspace 识别 userspace fastboot
    """
    state = get_device_state()

    if state == 'fastbootd':
        print_colored("[✓] 设备已在 fastbootd 模式", Colors.GREEN)
        return True

    if state == 'adb':
        print_colored("[*] 通过 adb 重启到 fastbootd...", Colors.BLUE)
        success, output = run_command(build_adb_command("reboot", "fastboot"))
        if not success:
            print_colored(f"[✗] adb reboot fastboot 失败: {output}", Colors.RED)
            return False
    elif state in ('fastboot', 'fastboot_unknown'):
        print_colored("[*] 通过 fastboot reboot fastboot 切换到 fastbootd...", Colors.BLUE)
        success, output = run_command(build_fastboot_command("reboot", "fastboot"), timeout=30)
        if not success:
            # 设备重启时 USB 断开可能导致 fastboot 命令返回非零状态码，
            # 不直接判定为失败，继续等待设备重新上线
            print_colored(f"    [!] reboot 命令返回异常: {output}", Colors.YELLOW)
            print_colored("    设备可能已开始重启，继续等待...", Colors.YELLOW)
        time.sleep(3)  # 等待设备断开 USB 连接
    else:
        print_colored("[✗] 未检测到任何设备", Colors.RED)
        return False

    # 刷完全部基础分区后设备需要加载新引导程序，120s 比默认 60s 更安全
    if not wait_for_fastboot_any(timeout=120):
        return False

    print_colored("[*] 正在确认 fastbootd 状态...", Colors.BLUE)
    confirm_deadline = time.time() + 45
    while time.time() < confirm_deadline:
        userspace = get_fastboot_userspace(TARGET_SERIAL)
        if userspace is True:
            print_colored("[✓] 已确认进入 fastbootd 模式", Colors.GREEN)
            return True
        if userspace is False:
            print_colored("    [!] 设备仍在 bootloader fastboot，继续等待切换...", Colors.YELLOW)
        else:
            print_colored("    [!] 尚无法确认 fastbootd 状态，继续重试...", Colors.YELLOW)
        time.sleep(3)

    print_colored(
        "[✗] 无法确认设备是否已进入 fastbootd，已停止后续 GSI 刷写以避免误刷动态分区",
        Colors.RED
    )
    print_colored("    请手动检查设备当前模式，确认已进入 fastbootd 后重新运行脚本", Colors.YELLOW)
    return False


def wait_for_fastboot_any(timeout: int = 60) -> bool:
    """
    等待设备进入 fastboot 或 fastbootd 模式
    （全志设备在 fastbootd 模式下也显示 fastboot）
    """
    return wait_for_device("fastboot_any", timeout=timeout)


def check_device() -> bool:
    """检查设备是否已连接到 fastboot"""
    print_colored("\n[*] 正在检测设备...", Colors.BLUE)
    fastboot_devices = list_fastboot_devices()
    device_serial = select_active_serial(fastboot_devices)

    if device_serial:
        print_colored(f"[✓] 已检测到设备: {device_serial}", Colors.GREEN)
        return True
    
    print_colored("[✗] 未检测到 fastboot 设备", Colors.RED)
    return False


def verify_firmware_files(firmware_dir: Path, partitions: list = None) -> Tuple[List[PartitionFile], List[str]]:
    """
    验证固件文件是否存在
    返回: (存在的文件列表, 缺失的必要文件列表)
    """
    if partitions is None:
        partitions = FLASH_PARTITIONS

    found_files = []
    missing_required = []

    print_colored("\n[*] 正在验证固件文件...", Colors.BLUE)

    for partition, filename, required in partitions:
        file_path = firmware_dir / filename
        if file_path.is_file():
            size_mb = file_path.stat().st_size / (1024 * 1024)
            print_colored(f"    [✓] {filename:<25} ({size_mb:.2f} MB)", Colors.GREEN)
            found_files.append((partition, filename, file_path))
        else:
            if file_path.exists():
                print_colored(f"    [✗] {filename:<25} (存在但不是普通文件!)", Colors.RED)
            if required:
                if not file_path.exists():
                    print_colored(f"    [✗] {filename:<25} (必需文件缺失!)", Colors.RED)
                missing_required.append(filename)
            else:
                if not file_path.exists():
                    print_colored(f"    [○] {filename:<25} (可选文件，已跳过)", Colors.YELLOW)
    
    return found_files, missing_required


def get_flash_timeout(file_path: Path, minimum_timeout: int = 600) -> int:
    """根据镜像大小估算更合理的刷写超时时间"""
    size_gib = file_path.stat().st_size / (1024 ** 3)
    timeout = max(minimum_timeout, int(size_gib * 900))

    if file_path.name in ["super.img", "system.img"]:
        timeout = max(timeout, 1800)

    return timeout


def flash_partition(partition: str, file_path: Path, index: int, total: int) -> bool:
    """刷写单个分区"""
    progress = f"[{index}/{total}]"
    print_colored(f"\n{progress} 正在刷写 {partition} <- {file_path.name}", Colors.BLUE)
    
    start_time = time.time()
    timeout = get_flash_timeout(file_path)
    print_colored(f"    超时设置: {timeout}s", Colors.CYAN)
    success, output = run_command(build_fastboot_command("flash", partition, str(file_path)), timeout=timeout)
    elapsed = time.time() - start_time
    
    if success:
        print_colored(f"    [✓] 完成 (耗时 {elapsed:.1f}s)", Colors.GREEN)
        return True
    else:
        print_colored(f"    [✗] 失败: {output}", Colors.RED)
        return False


def flash_gsi(system_img: Path) -> bool:
    """
    刷写 GSI system.img
    按照验证成功的流程:
    1. adb reboot fastboot (进入 fastbootd 模式)
    2. fastboot flash system_a system.img
    3. 是否执行 fastboot -w 由主流程统一决定
    """
    print_colored("\n" + "="*60, Colors.MAGENTA)
    print_colored("    刷写 GSI System Image", Colors.BOLD + Colors.MAGENTA)
    print_colored("="*60, Colors.MAGENTA)
    
    if not reboot_to_fastbootd():
        return False
    
    # 刷写 GSI
    print_colored(f"\n[*] 正在刷写 GSI: {system_img.name}", Colors.BLUE)
    size_mb = system_img.stat().st_size / (1024 * 1024)
    print_colored(f"    文件大小: {size_mb:.2f} MB", Colors.CYAN)
    print_colored("    目标分区: system_a", Colors.CYAN)
    
    start_time = time.time()
    timeout = get_flash_timeout(system_img)
    print_colored(f"    超时设置: {timeout}s", Colors.CYAN)
    success, output = run_command(build_fastboot_command("flash", "system_a", str(system_img)), timeout=timeout)
    elapsed = time.time() - start_time
    
    if not success:
        print_colored(f"    [✗] GSI 刷写失败: {output}", Colors.RED)
        return False
    
    print_colored(f"    [✓] GSI 刷写完成 (耗时 {elapsed:.1f}s)", Colors.GREEN)
    
    return True


def flash_vts(vendor_boot_debug: Path) -> bool:
    """刷写 VTS vendor_boot-debug.img"""
    print_colored("\n" + "="*60, Colors.MAGENTA)
    print_colored("    刷写 VTS vendor_boot-debug Image", Colors.BOLD + Colors.MAGENTA)
    print_colored("="*60, Colors.MAGENTA)
    
    # 确保在 fastboot 模式
    state = get_device_state()
    if state not in ['fastboot', 'fastbootd']:
        if not reboot_to_bootloader():
            return False
    
    print_colored(f"\n[*] 正在刷写 VTS: {vendor_boot_debug.name}", Colors.BLUE)
    size_mb = vendor_boot_debug.stat().st_size / (1024 * 1024)
    print_colored(f"    文件大小: {size_mb:.2f} MB", Colors.CYAN)
    
    start_time = time.time()
    timeout = get_flash_timeout(vendor_boot_debug, minimum_timeout=300)
    print_colored(f"    超时设置: {timeout}s", Colors.CYAN)
    success, output = run_command(build_fastboot_command("flash", "vendor_boot", str(vendor_boot_debug)), timeout=timeout)
    elapsed = time.time() - start_time
    
    if success:
        print_colored(f"    [✓] VTS 刷写完成 (耗时 {elapsed:.1f}s)", Colors.GREEN)
        return True
    else:
        print_colored(f"    [✗] VTS 刷写失败: {output}", Colors.RED)
        return False


def wipe_userdata() -> bool:
    """清除用户数据"""
    print_colored("\n[*] 正在清除用户数据 (-w)...", Colors.BLUE)
    success, output = run_command(build_fastboot_command("-w"), timeout=180)
    if success:
        print_colored("    [✓] 用户数据清除完成", Colors.GREEN)
        return True
    else:
        print_colored(f"    [✗] 清除失败: {output}", Colors.RED)
        return False


def reboot_device() -> bool:
    """重启设备"""
    print_colored("\n[*] 正在重启设备...", Colors.BLUE)
    success, output = run_command(build_fastboot_command("reboot"), timeout=30)
    if success:
        print_colored("    [✓] 设备正在重启", Colors.GREEN)
        return True
    else:
        print_colored(f"    [✗] 重启失败: {output}", Colors.RED)
        return False


def confirm_action(message: str) -> bool:
    """确认操作"""
    while True:
        response = input(f"{Colors.YELLOW}{message} (y/n): {Colors.RESET}").strip().lower()
        if response in ['y', 'yes', '是']:
            return True
        elif response in ['n', 'no', '否']:
            return False
        print("请输入 y 或 n")


def select_option(options: List[str], prompt: str) -> int:
    """让用户选择选项，返回选项索引"""
    print_colored(f"\n{prompt}", Colors.YELLOW)
    for i, option in enumerate(options, 1):
        print_colored(f"    {i}. {option}", Colors.CYAN)
    
    while True:
        try:
            choice = input(f"{Colors.YELLOW}请输入选项 (1-{len(options)}): {Colors.RESET}").strip()
            idx = int(choice)
            if 1 <= idx <= len(options):
                return idx - 1
            print(f"请输入 1 到 {len(options)} 之间的数字")
        except ValueError:
            print("请输入有效的数字")


def detect_extra_images(
    firmware_dir: Path,
    gsi_path: Optional[Path],
    vts_path: Optional[Path],
    allow_gsi_autodetect: bool = True,
) -> Tuple[Optional[Path], Optional[Path]]:
    """检测目录中的额外镜像文件"""
    detected_gsi = None
    detected_vts = None
    
    # 检测 GSI (system.img)
    if gsi_path is None:
        system_img = firmware_dir / "system.img"
        if system_img.is_file() and allow_gsi_autodetect:
            size_mb = system_img.stat().st_size / (1024 * 1024)
            print_colored(f"\n[!] 检测到 system.img ({size_mb:.2f} MB)", Colors.YELLOW)
            if confirm_action("是否要刷入此 GSI 镜像?"):
                detected_gsi = system_img
        elif system_img.is_file():
            print_colored(
                "\n[*] 当前目录已包含完整基础固件，默认不将 system.img 自动识别为额外 GSI；如需叠刷请显式传 --gsi",
                Colors.CYAN
            )
    else:
        detected_gsi = gsi_path
    
    # 检测 VTS (vendor_boot-debug.img)
    if vts_path is None:
        vts_img = firmware_dir / "vendor_boot-debug.img"
        if vts_img.is_file():
            size_mb = vts_img.stat().st_size / (1024 * 1024)
            print_colored(f"\n[!] 检测到 vendor_boot-debug.img ({size_mb:.2f} MB)", Colors.YELLOW)
            if confirm_action("是否要刷入此 VTS 镜像?"):
                detected_vts = vts_img
    else:
        detected_vts = vts_path
    
    return detected_gsi, detected_vts


def select_flash_mode(has_firmware: bool, has_gsi: bool, has_vts: bool) -> FlashMode:
    """根据可用镜像让用户选择刷机模式"""
    options = []
    modes = []
    
    if has_firmware:
        options.append("完整刷机（所有基础分区）")
        modes.append(FlashMode.FULL)
    
    if has_gsi:
        options.append("仅刷 GSI (system.img)")
        modes.append(FlashMode.GSI_ONLY)
    
    if has_vts:
        options.append("仅刷 VTS (vendor_boot-debug.img)")
        modes.append(FlashMode.VTS_ONLY)
    
    if has_firmware and has_gsi:
        options.append("完整刷机 + GSI")
        modes.append(FlashMode.FULL_WITH_GSI)
    
    if has_firmware and has_vts:
        options.append("完整刷机 + VTS")
        modes.append(FlashMode.FULL_WITH_VTS)
    
    if has_gsi and has_vts:
        options.append("GSI + VTS（不刷基础分区）")
        modes.append(FlashMode.GSI_WITH_VTS)
    
    if has_firmware and has_gsi and has_vts:
        options.append("完整刷机 + GSI + VTS（全部刷入）")
        modes.append(FlashMode.FULL_WITH_ALL)
    
    if len(options) == 1:
        return modes[0]
    
    idx = select_option(options, "请选择刷机模式:")
    return modes[idx]


def is_full_flash_mode(mode: FlashMode) -> bool:
    """判断是否包含基础固件刷写"""
    return mode in [
        FlashMode.FULL,
        FlashMode.FULL_WITH_GSI,
        FlashMode.FULL_WITH_VTS,
        FlashMode.FULL_WITH_ALL,
    ]


def flash_full(found_files: List[PartitionFile]) -> Tuple[int, List[str]]:
    """执行完整刷机"""
    print_colored("\n" + "="*60, Colors.CYAN)
    print_colored("    开始刷机流程", Colors.BOLD + Colors.CYAN)
    print_colored("="*60, Colors.CYAN)
    
    failed_partitions = []
    
    for index, (partition, filename, file_path) in enumerate(found_files, 1):
        if not flash_partition(partition, file_path, index, len(found_files)):
            failed_partitions.append(partition)
            if not confirm_action(f"分区 {partition} 刷写失败，是否继续?"):
                return len(found_files) - len(failed_partitions), failed_partitions
    
    return len(found_files) - len(failed_partitions), failed_partitions


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Allwinner Fastboot Flash Tool - 全志平台固件自动刷写工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 标准刷机
    python aw_fastboot_flash.py D:\\firmware\\output

    # 安全固件刷机
    python aw_fastboot_flash.py D:\\firmware\\output -s

    # 带 GSI 刷机
    python aw_fastboot_flash.py D:\\firmware\\output --gsi D:\\gsi\\system.img

    # 带 VTS 刷机
    python aw_fastboot_flash.py D:\\firmware\\output --vts D:\\vts\\vendor_boot-debug.img

    # 组合刷机
    python aw_fastboot_flash.py D:\\firmware\\output --gsi D:\\gsi\\system.img --vts D:\\vts\\vendor_boot-debug.img
        """
    )
    
    parser.add_argument(
        "firmware_path",
        type=str,
        help="固件目录路径或全志打包镜像文件路径（.img），"
             "指定镜像文件时自动解包后刷机"
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        metavar="DIR",
        help="解包镜像的输出目录（默认: 当前目录下的 output）"
    )
    
    parser.add_argument(
        "--gsi",
        type=str,
        metavar="PATH",
        help="GSI system.img 文件路径"
    )
    
    parser.add_argument(
        "--vts",
        type=str,
        metavar="PATH",
        help="VTS vendor_boot-debug.img 文件路径"
    )

    parser.add_argument(
        "-s", "--secure",
        action="store_true",
        help="安全固件模式: 用户确认后执行 OEM 解锁，"
             "boot0 刷入 toc0.fex，u-boot 刷入 toc1.fex"
    )

    parser.add_argument(
        "--serial",
        type=str,
        metavar="SERIAL",
        help="指定目标设备序列号；连接多台设备时必须提供"
    )
    
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="整个刷机流程完成后不清除用户数据"
    )
    
    parser.add_argument(
        "--no-reboot",
        action="store_true",
        help="刷机完成后不重启设备"
    )
    
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()
    
    firmware_path = Path(args.firmware_path).resolve()
    gsi_path = Path(args.gsi).resolve() if args.gsi else None
    vts_path = Path(args.vts).resolve() if args.vts else None
    secure = args.secure

    # 验证路径
    if not firmware_path.exists():
        print_colored(f"[✗] 错误: 路径不存在: {firmware_path}", Colors.RED)
        sys.exit(1)

    try:
        gsi_path = normalize_optional_image_path(gsi_path, "GSI", "system.img")
        vts_path = normalize_optional_image_path(vts_path, "VTS", "vendor_boot-debug.img")
    except (FileNotFoundError, ValueError) as e:
        print_colored(f"[✗] 错误: {e}", Colors.RED)
        sys.exit(1)

    log_file = setup_logging()
    print_header()

    # 判断输入是镜像文件还是目录，镜像文件需先解包
    extracted_output_dir = None
    if firmware_path.is_file():
        output_dir = Path(args.output_dir).resolve() if args.output_dir else Path.cwd() / "output"
        extracted = unpack_aw_img(firmware_path, output_dir)
        if not extracted:
            print_colored("[✗] 解包失败，未提取到任何固件文件", Colors.RED)
            sys.exit(1)
        firmware_dir = output_dir
        extracted_output_dir = output_dir
    else:
        firmware_dir = firmware_path

    print_colored(f"[*] 固件目录: {firmware_dir}", Colors.BLUE)
    if extracted_output_dir:
        print_colored(f"[*] 镜像来源: {firmware_path.name}", Colors.BLUE)
    if secure:
        print_colored("[*] 固件类型: 安全固件 (-s)", Colors.BOLD + Colors.YELLOW)
    if gsi_path:
        print_colored(f"[*] GSI 路径: {gsi_path}", Colors.BLUE)
    if vts_path:
        print_colored(f"[*] VTS 路径: {vts_path}", Colors.BLUE)
    if log_file:
        print_colored(f"[*] 日志文件: {log_file}", Colors.BLUE)
    
    # 检查工具
    if not check_adb() or not check_fastboot():
        sys.exit(1)

    if not resolve_target_serial(args.serial):
        sys.exit(1)
    
    # 验证固件文件
    flash_partitions = get_flash_partitions(secure)
    found_files, missing_required = verify_firmware_files(firmware_dir, flash_partitions)
    has_firmware = len(found_files) > 0 and len(missing_required) == 0
    
    if missing_required:
        print_colored(f"\n[!] 缺少 {len(missing_required)} 个必需的基础固件文件", Colors.YELLOW)
        if not gsi_path and not vts_path:
            # 检测目录中的额外镜像
            gsi_path, vts_path = detect_extra_images(
                firmware_dir,
                gsi_path,
                vts_path,
                allow_gsi_autodetect=True,
            )
            if not gsi_path and not vts_path:
                print_colored("[✗] 没有可刷写的镜像，退出", Colors.RED)
                sys.exit(1)
    else:
        # 检测目录中的额外镜像
        gsi_path, vts_path = detect_extra_images(
            firmware_dir,
            gsi_path,
            vts_path,
            allow_gsi_autodetect=False,
        )
    
    has_gsi = gsi_path is not None
    has_vts = vts_path is not None
    
    # 选择刷机模式
    mode = select_flash_mode(has_firmware, has_gsi, has_vts)
    
    print_colored(f"\n[*] 选择的刷机模式: {mode.name}", Colors.CYAN)

    if secure and not is_full_flash_mode(mode):
        print_colored("[✗] 安全固件模式仅支持包含基础固件刷写的模式", Colors.RED)
        print_colored("    请使用 完整刷机 / 完整刷机 + GSI / 完整刷机 + VTS / 完整刷机 + GSI + VTS", Colors.YELLOW)
        sys.exit(1)
    
    # 自动进入 bootloader 模式
    print_colored("\n" + "-"*60, Colors.CYAN)
    print_colored("准备设备", Colors.BOLD + Colors.CYAN)
    print_colored("-"*60, Colors.CYAN)
    
    if mode in [FlashMode.GSI_ONLY, FlashMode.GSI_WITH_VTS]:
        # GSI 单刷需要先进入 fastbootd
        if not reboot_to_fastbootd():
            sys.exit(1)
    else:
        # 其他模式先进入 bootloader
        if not reboot_to_bootloader():
            sys.exit(1)

    # 确认刷机
    print_colored(f"\n{'='*60}", Colors.YELLOW)
    print_colored("刷机确认", Colors.BOLD + Colors.YELLOW)
    print_colored(f"{'='*60}", Colors.YELLOW)
    
    summary = []
    if secure:
        summary.append("固件类型: 安全固件（将先执行 OEM 解锁，并用 toc0/toc1 替换 boot0/u-boot）")
    if is_full_flash_mode(mode):
        summary.append(f"基础分区: {len(found_files)} 个")
    if mode in [FlashMode.GSI_ONLY, FlashMode.FULL_WITH_GSI, FlashMode.GSI_WITH_VTS, FlashMode.FULL_WITH_ALL]:
        summary.append(f"GSI: {gsi_path.name}")
    if mode in [FlashMode.VTS_ONLY, FlashMode.FULL_WITH_VTS, FlashMode.GSI_WITH_VTS, FlashMode.FULL_WITH_ALL]:
        summary.append(f"VTS: {vts_path.name}")
    
    for item in summary:
        print_colored(f"  • {item}", Colors.CYAN)

    print_colored(f"  • 目标设备: {TARGET_SERIAL}", Colors.CYAN)
    
    should_wipe = (not args.no_wipe) and mode != FlashMode.VTS_ONLY

    if should_wipe:
        print_colored("  • 刷机完成后将清除用户数据", Colors.YELLOW)
    
    if not confirm_action("\n是否继续?"):
        print_colored("\n[*] 操作已取消", Colors.YELLOW)
        sys.exit(0)

    if secure:
        if not confirm_action("安全固件模式将执行 OEM 解锁，设备可能要求人工确认并可能清除数据，是否继续?"):
            print_colored("\n[*] 操作已取消", Colors.YELLOW)
            sys.exit(0)
    
    # 开始刷机
    start_time = time.time()
    success_count = 0
    failed_partitions = []

    if secure:
        if not oem_unlock():
            print_colored("[✗] OEM 解锁失败，无法继续刷写安全固件", Colors.RED)
            sys.exit(1)
        if not recover_after_oem_unlock():
            print_colored("[✗] OEM 解锁后设备状态异常，无法继续刷写安全固件", Colors.RED)
            sys.exit(1)
    
    # 刷写基础分区
    if is_full_flash_mode(mode):
        count, failed = flash_full(found_files)
        success_count += count
        failed_partitions.extend(failed)
    
    # 刷写 GSI
    if mode in [FlashMode.GSI_ONLY, FlashMode.FULL_WITH_GSI, FlashMode.GSI_WITH_VTS, FlashMode.FULL_WITH_ALL]:
        if flash_gsi(gsi_path):
            success_count += 1
        else:
            failed_partitions.append("system_a (GSI)")
    
    # 刷写 VTS
    if mode in [FlashMode.VTS_ONLY, FlashMode.FULL_WITH_VTS, FlashMode.GSI_WITH_VTS, FlashMode.FULL_WITH_ALL]:
        if flash_vts(vts_path):
            success_count += 1
        else:
            failed_partitions.append("vendor_boot (VTS)")

    if should_wipe:
        if failed_partitions:
            print_colored("[!] 存在失败项，默认跳过用户数据清除以保留现场", Colors.YELLOW)
        elif not wipe_userdata():
            failed_partitions.append("userdata (-w)")
    
    # 重启设备
    if not args.no_reboot:
        if failed_partitions:
            print_colored("[!] 存在失败项，默认跳过重启，请保留当前状态排查", Colors.YELLOW)
        else:
            reboot_device()
    
    # 打印结果
    elapsed = time.time() - start_time
    print_colored("\n" + "="*60, Colors.CYAN)
    print_colored("    刷机完成", Colors.BOLD + Colors.CYAN)
    print_colored("="*60, Colors.CYAN)
    
    print_colored(f"\n[*] 总耗时: {elapsed:.1f} 秒", Colors.BLUE)
    print_colored(f"[*] 成功刷写: {success_count} 个分区/镜像", Colors.GREEN)
    
    if failed_partitions:
        print_colored(f"[!] 失败: {', '.join(failed_partitions)}", Colors.RED)
    else:
        print_colored("[✓] 所有镜像刷写成功!", Colors.GREEN)
    
    if not args.no_reboot and not failed_partitions:
        print_colored("\n设备正在重启，请等待启动完成...", Colors.CYAN)

    # 解包模式下提示是否清理输出目录
    if extracted_output_dir and extracted_output_dir.exists():
        if confirm_action(f"\n是否删除解包输出目录 ({extracted_output_dir})?"):
            shutil.rmtree(extracted_output_dir)
            print_colored("[✓] 输出目录已删除", Colors.GREEN)
        else:
            print_colored(f"[*] 输出目录已保留: {extracted_output_dir}", Colors.CYAN)


if __name__ == "__main__":
    enable_windows_ansi_colors()
    
    try:
        main()
    except KeyboardInterrupt:
        print_colored("\n\n[*] 操作已被用户中断", Colors.YELLOW)
        sys.exit(1)
