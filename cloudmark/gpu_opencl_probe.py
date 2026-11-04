from __future__ import annotations

import ctypes
import ctypes.util
import json
import platform
import re
from typing import Any


OPENCL_PROBE_VERSION = "gpu-opencl-probe-v1"
OPENCL_MAX_PLATFORMS = 8
OPENCL_MAX_DEVICES = 16
OPENCL_MAX_TEXT_BYTES = 4_096
OPENCL_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")

CL_SUCCESS = 0
CL_DEVICE_NOT_FOUND = -1
CL_DEVICE_TYPE_GPU = 1 << 2
CL_PLATFORM_NAME = 0x0902
CL_PLATFORM_VENDOR = 0x0903
CL_PLATFORM_VERSION = 0x0901
CL_DEVICE_MAX_MEM_ALLOC_SIZE = 0x1010
CL_DEVICE_GLOBAL_MEM_SIZE = 0x101F
CL_DEVICE_AVAILABLE = 0x1027
CL_DEVICE_COMPILER_AVAILABLE = 0x1028
CL_DEVICE_NAME = 0x102B
CL_DEVICE_VENDOR = 0x102C
CL_DRIVER_VERSION = 0x102D
CL_DEVICE_VERSION = 0x102F
CL_DEVICE_ADDRESS_BITS = 0x100D
CL_DEVICE_HOST_UNIFIED_MEMORY = 0x1035
CL_DEVICE_OPENCL_C_VERSION = 0x103D

cl_platform_id = ctypes.c_void_p
cl_device_id = ctypes.c_void_p
cl_uint = ctypes.c_uint
cl_int = ctypes.c_int
cl_ulong = ctypes.c_ulonglong
cl_bool = ctypes.c_uint
size_t = ctypes.c_size_t


def _library_candidates() -> list[str]:
    candidates: list[str] = []
    discovered = ctypes.util.find_library("OpenCL")
    if discovered:
        candidates.append(discovered)
    if platform.system() == "Darwin":
        candidates.append("/System/Library/Frameworks/OpenCL.framework/OpenCL")
    elif platform.system() != "Windows":
        candidates.extend(["libOpenCL.so.1", "libOpenCL.so"])
    return list(dict.fromkeys(candidates))


def _load_opencl() -> ctypes.CDLL:
    if platform.system() == "Windows":
        try:
            return ctypes.WinDLL("OpenCL.dll", winmode=0x00000800)  # type: ignore[attr-defined,return-value]
        except OSError as exc:
            raise RuntimeError("opencl-loader-unavailable") from exc
    for candidate in _library_candidates():
        try:
            return ctypes.CDLL(candidate)
        except OSError:
            continue
    raise RuntimeError("opencl-loader-unavailable")


def _bind(library: ctypes.CDLL) -> None:
    library.clGetPlatformIDs.argtypes = [cl_uint, ctypes.POINTER(cl_platform_id), ctypes.POINTER(cl_uint)]
    library.clGetPlatformIDs.restype = cl_int
    library.clGetPlatformInfo.argtypes = [cl_platform_id, cl_uint, size_t, ctypes.c_void_p, ctypes.POINTER(size_t)]
    library.clGetPlatformInfo.restype = cl_int
    library.clGetDeviceIDs.argtypes = [
        cl_platform_id,
        cl_ulong,
        cl_uint,
        ctypes.POINTER(cl_device_id),
        ctypes.POINTER(cl_uint),
    ]
    library.clGetDeviceIDs.restype = cl_int
    library.clGetDeviceInfo.argtypes = [cl_device_id, cl_uint, size_t, ctypes.c_void_p, ctypes.POINTER(size_t)]
    library.clGetDeviceInfo.restype = cl_int


def _safe_text(value: bytes) -> str | None:
    try:
        text = value.rstrip(b"\x00").decode("utf-8")
    except UnicodeDecodeError:
        return None
    normalized = " ".join(text.split())
    return normalized if OPENCL_SAFE_TEXT.fullmatch(normalized) else None


def _info_text(function: Any, handle: ctypes.c_void_p, parameter: int) -> str | None:
    length = size_t()
    if function(handle, parameter, 0, None, ctypes.byref(length)) != CL_SUCCESS:
        return None
    if not 1 <= length.value <= OPENCL_MAX_TEXT_BYTES:
        return None
    buffer = ctypes.create_string_buffer(length.value)
    if function(handle, parameter, length.value, buffer, None) != CL_SUCCESS:
        return None
    return _safe_text(buffer.raw)


def _info_scalar(function: Any, handle: ctypes.c_void_p, parameter: int, scalar: Any) -> int | None:
    value = scalar()
    if function(handle, parameter, ctypes.sizeof(value), ctypes.byref(value), None) != CL_SUCCESS:
        return None
    return int(value.value)


def collect_opencl_probe() -> dict[str, Any]:
    library = _load_opencl()
    _bind(library)
    platform_count = cl_uint()
    if library.clGetPlatformIDs(0, None, ctypes.byref(platform_count)) != CL_SUCCESS:
        raise RuntimeError("opencl-platform-enumeration-failed")
    if platform_count.value > OPENCL_MAX_PLATFORMS:
        raise RuntimeError("opencl-platform-count-outside-policy")
    platforms: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    if platform_count.value:
        handles = (cl_platform_id * platform_count.value)()
        if library.clGetPlatformIDs(platform_count.value, handles, None) != CL_SUCCESS:
            raise RuntimeError("opencl-platform-read-failed")
        for platform_index, platform_handle in enumerate(handles):
            platform_name = _info_text(library.clGetPlatformInfo, platform_handle, CL_PLATFORM_NAME)
            platform_vendor = _info_text(library.clGetPlatformInfo, platform_handle, CL_PLATFORM_VENDOR)
            platform_version = _info_text(library.clGetPlatformInfo, platform_handle, CL_PLATFORM_VERSION)
            platforms.append(
                {
                    "index": platform_index,
                    "name": platform_name,
                    "vendor": platform_vendor,
                    "version": platform_version,
                    "status": (
                        "observed" if platform_name and platform_vendor and platform_version else "partial"
                    ),
                }
            )
            device_count = cl_uint()
            code = library.clGetDeviceIDs(
                platform_handle,
                CL_DEVICE_TYPE_GPU,
                0,
                None,
                ctypes.byref(device_count),
            )
            if code == CL_DEVICE_NOT_FOUND:
                continue
            if code != CL_SUCCESS:
                raise RuntimeError("opencl-device-enumeration-failed")
            if len(devices) + device_count.value > OPENCL_MAX_DEVICES:
                raise RuntimeError("opencl-device-count-outside-policy")
            if not device_count.value:
                continue
            device_handles = (cl_device_id * device_count.value)()
            if library.clGetDeviceIDs(
                platform_handle,
                CL_DEVICE_TYPE_GPU,
                device_count.value,
                device_handles,
                None,
            ) != CL_SUCCESS:
                raise RuntimeError("opencl-device-read-failed")
            for platform_device_index, device_handle in enumerate(device_handles):
                name = _info_text(library.clGetDeviceInfo, device_handle, CL_DEVICE_NAME)
                vendor = _info_text(library.clGetDeviceInfo, device_handle, CL_DEVICE_VENDOR)
                driver_version = _info_text(library.clGetDeviceInfo, device_handle, CL_DRIVER_VERSION)
                device_version = _info_text(library.clGetDeviceInfo, device_handle, CL_DEVICE_VERSION)
                opencl_c_version = _info_text(library.clGetDeviceInfo, device_handle, CL_DEVICE_OPENCL_C_VERSION)
                global_memory = _info_scalar(
                    library.clGetDeviceInfo,
                    device_handle,
                    CL_DEVICE_GLOBAL_MEM_SIZE,
                    cl_ulong,
                )
                max_allocation = _info_scalar(
                    library.clGetDeviceInfo,
                    device_handle,
                    CL_DEVICE_MAX_MEM_ALLOC_SIZE,
                    cl_ulong,
                )
                available = _info_scalar(
                    library.clGetDeviceInfo,
                    device_handle,
                    CL_DEVICE_AVAILABLE,
                    cl_bool,
                )
                compiler_available = _info_scalar(
                    library.clGetDeviceInfo,
                    device_handle,
                    CL_DEVICE_COMPILER_AVAILABLE,
                    cl_bool,
                )
                unified_memory = _info_scalar(
                    library.clGetDeviceInfo,
                    device_handle,
                    CL_DEVICE_HOST_UNIFIED_MEMORY,
                    cl_bool,
                )
                address_bits = _info_scalar(
                    library.clGetDeviceInfo,
                    device_handle,
                    CL_DEVICE_ADDRESS_BITS,
                    cl_uint,
                )
                required = (name, vendor, driver_version, device_version, global_memory, max_allocation)
                devices.append(
                    {
                        "index": len(devices),
                        "platform_index": platform_index,
                        "platform_device_index": platform_device_index,
                        "status": "observed" if all(value is not None for value in required) else "partial",
                        "name": name,
                        "vendor": vendor,
                        "driver_version": driver_version,
                        "device_version": device_version,
                        "opencl_c_version": opencl_c_version,
                        "global_memory_bytes": global_memory,
                        "max_allocation_bytes": max_allocation,
                        "address_bits": address_bits,
                        "available": bool(available) if available is not None else None,
                        "compiler_available": (
                            bool(compiler_available) if compiler_available is not None else None
                        ),
                        "unified_memory": bool(unified_memory) if unified_memory is not None else None,
                    }
                )
    complete = all(item["status"] == "observed" for item in (*platforms, *devices))
    return {
        "methodology_version": OPENCL_PROBE_VERSION,
        "status": "complete" if complete else "partial",
        "platform_count": len(platforms),
        "device_count": len(devices),
        "platforms": platforms,
        "devices": devices,
        "policy": {
            "read_only": True,
            "context_created": False,
            "queue_created": False,
            "buffer_allocated": False,
            "kernel_compiled": False,
            "load_generated": False,
            "extension_list_persisted": False,
        },
    }


def main() -> None:
    try:
        result = collect_opencl_probe()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        result = {
            "methodology_version": OPENCL_PROBE_VERSION,
            "status": "unavailable",
            "platform_count": 0,
            "device_count": 0,
            "platforms": [],
            "devices": [],
            "reason_code": "opencl-probe-unavailable",
            "policy": {
                "read_only": True,
                "context_created": False,
                "queue_created": False,
                "buffer_allocated": False,
                "kernel_compiled": False,
                "load_generated": False,
                "extension_list_persisted": False,
            },
        }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
