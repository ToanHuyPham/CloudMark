"""Isolated fixed OpenCL copy workload. Invoked only after explicit confirmation."""
from __future__ import annotations

import argparse
import ctypes as c
import hashlib
import json
import time

from .gpu_opencl_probe import _load_opencl, _bind, collect_opencl_probe, CL_DEVICE_TYPE_GPU
from .gpu_opencl_probe import _info_text, _info_scalar, cl_ulong, cl_bool, cl_uint
from . import gpu_opencl_probe as probe_api
from .gpu_transfer import BUFFER_BYTES, ITERATIONS, HOST_RESERVE_BYTES, plan_gpu_transfer
from .compute import _memory_available_bytes, _memory_allocation_boundary


def host_headroom() -> int:
    import os
    if os.name == "nt":
        class MemoryStatus(c.Structure):
            _fields_ = [("length", c.c_uint32), ("load", c.c_uint32)] + [
                (name, c.c_uint64) for name in (
                    "total", "available", "total_page", "available_page", "total_virtual",
                    "available_virtual", "extended",
                )
            ]
        status = MemoryStatus()
        status.length = c.sizeof(status)
        if not c.windll.kernel32.GlobalMemoryStatusEx(c.byref(status)):
            raise ValueError("Host memory headroom unavailable.")
        available = status.available
    else:
        boundary = _memory_allocation_boundary(_memory_available_bytes())
        available = boundary.get("effective_available_bytes")
        if not boundary.get("cgroup_headroom_verified"):
            raise ValueError("Host memory boundary unverified.")
    if type(available) is not int or available < HOST_RESERVE_BYTES + 4 * BUFFER_BYTES:
        raise ValueError("Insufficient verified host memory headroom.")
    return available


def bind_transfer(lib):
    pointer, uint, size, integer = c.c_void_p, c.c_uint, c.c_size_t, c.c_int
    signatures = {
        "clCreateContext": ([pointer, uint, c.POINTER(pointer), pointer, pointer, c.POINTER(integer)], pointer),
        "clCreateCommandQueue": ([pointer, pointer, c.c_ulonglong, c.POINTER(integer)], pointer),
        "clCreateBuffer": ([pointer, c.c_ulonglong, size, pointer, c.POINTER(integer)], pointer),
        "clEnqueueWriteBuffer": ([pointer, pointer, uint, size, size, pointer, uint, pointer, pointer], integer),
        "clEnqueueReadBuffer": ([pointer, pointer, uint, size, size, pointer, uint, pointer, pointer], integer),
        "clEnqueueCopyBuffer": ([pointer, pointer, pointer, size, size, size, uint, pointer, pointer], integer),
        "clFinish": ([pointer], integer),
        "clReleaseMemObject": ([pointer], integer),
        "clReleaseCommandQueue": ([pointer], integer),
        "clReleaseContext": ([pointer], integer),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result


def check(code):
    if code != 0:
        raise RuntimeError(f"OpenCL transfer failed with status {code}.")


def verify_device(lib, device, expected):
    fields = {
        "name": probe_api.CL_DEVICE_NAME, "vendor": probe_api.CL_DEVICE_VENDOR,
        "driver_version": probe_api.CL_DRIVER_VERSION, "device_version": probe_api.CL_DEVICE_VERSION,
    }
    for key, parameter in fields.items():
        if _info_text(lib.clGetDeviceInfo, device, parameter) != expected[key]:
            raise ValueError("Selected OpenCL device identity changed before allocation.")
    for key, parameter, scalar in (
        ("global_memory_bytes", probe_api.CL_DEVICE_GLOBAL_MEM_SIZE, cl_ulong),
        ("max_allocation_bytes", probe_api.CL_DEVICE_MAX_MEM_ALLOC_SIZE, cl_ulong),
        ("available", probe_api.CL_DEVICE_AVAILABLE, cl_bool),
        ("unified_memory", probe_api.CL_DEVICE_HOST_UNIFIED_MEMORY, cl_bool),
        ("address_bits", probe_api.CL_DEVICE_ADDRESS_BITS, cl_uint),
    ):
        if _info_scalar(lib.clGetDeviceInfo, device, parameter, scalar) != expected[key]:
            raise ValueError("Selected OpenCL device limits changed before allocation.")


def execute_transfers(lib, device, checkpoint):
    """Two fixed buffers; synchronous wall-clock timings include host API cost."""
    resources = []
    measurements = []
    error = c.c_int()
    source = (c.c_ubyte * BUFFER_BYTES)()
    destination = (c.c_ubyte * BUFFER_BYTES)()
    # SHAKE gives deterministic non-repeating data without a large Python integer list.
    payload = hashlib.shake_256(b"cloudmark-gpu-transfer-v1").digest(BUFFER_BYTES)
    c.memmove(source, payload, BUFFER_BYTES)
    expected = hashlib.sha256(payload).hexdigest()
    del payload
    cleanup_ok = True
    try:
        checkpoint()
        context = lib.clCreateContext(None, 1, (c.c_void_p * 1)(device), None, None, c.byref(error))
        if context:
            resources.append((lib.clReleaseContext, context))
        check(error.value)
        if not context:
            raise RuntimeError("OpenCL returned a null context.")
        queue = lib.clCreateCommandQueue(context, device, 0, c.byref(error))
        if queue:
            resources.append((lib.clReleaseCommandQueue, queue))
        check(error.value)
        if not queue:
            raise RuntimeError("OpenCL returned a null queue.")
        buffers = []
        for _ in range(2):
            buffer = lib.clCreateBuffer(context, 1, BUFFER_BYTES, None, c.byref(error))
            if buffer:
                resources.append((lib.clReleaseMemObject, buffer))
            check(error.value)
            if not buffer:
                raise RuntimeError("OpenCL returned a null buffer.")
            buffers.append(buffer)
        first, second = buffers

        def write():
            check(lib.clEnqueueWriteBuffer(queue, first, 1, 0, BUFFER_BYTES, source, 0, None, None))

        def read(buffer=first):
            check(lib.clEnqueueReadBuffer(queue, buffer, 1, 0, BUFFER_BYTES, destination, 0, None, None))

        def copy():
            check(lib.clEnqueueCopyBuffer(queue, first, second, 0, 0, BUFFER_BYTES, 0, None, None))
            check(lib.clFinish(queue))

        for name, operation in (("host-to-device", write), ("device-to-host", read), ("device-to-device", copy)):
            checkpoint()
            operation()  # one untimed warm-up
            samples = []
            for _ in range(ITERATIONS):
                checkpoint()
                started = time.perf_counter_ns()
                operation()
                elapsed = time.perf_counter_ns() - started
                if elapsed <= 0:
                    raise RuntimeError("Invalid transfer duration.")
                samples.append(elapsed)
            read(second if name == "device-to-device" else first)
            if hashlib.sha256(destination).hexdigest() != expected:
                raise RuntimeError("GPU transfer checksum mismatch.")
            measurements.append({
                "name": name, "samples_ns": samples, "bytes_per_iteration": BUFFER_BYTES,
                "iterations": ITERATIONS, "sha256": expected, "integrity_verified": True,
            })
    finally:
        for release, handle in reversed(resources):
            try:
                cleanup_ok = release(handle) == 0 and cleanup_ok
            except Exception:
                cleanup_ok = False
    if not cleanup_ok:
        raise RuntimeError("OpenCL resource release could not be verified.")
    return {"measurements": measurements, "cleanup_verified": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-index", type=int, required=True)
    parser.add_argument("--confirm-load", action="store_true", required=True)
    args = parser.parse_args()
    started = time.monotonic()

    def checkpoint():
        if time.monotonic() - started > 60:
            raise RuntimeError("GPU transfer worker deadline exceeded.")

    probe = collect_opencl_probe()
    plan = plan_gpu_transfer(probe, args.device_index)
    available = host_headroom()
    lib = _load_opencl()
    _bind(lib)
    count = c.c_uint()
    check(lib.clGetPlatformIDs(0, None, c.byref(count)))
    if count.value != probe["platform_count"]:
        raise ValueError("OpenCL platform enumeration changed.")
    platforms = (c.c_void_p * count.value)()
    check(lib.clGetPlatformIDs(count.value, platforms, None))
    platform = platforms[plan["device"]["platform_index"]]
    check(lib.clGetDeviceIDs(platform, CL_DEVICE_TYPE_GPU, 0, None, c.byref(count)))
    expected_count = sum(d["platform_index"] == plan["device"]["platform_index"] for d in probe["devices"])
    if count.value != expected_count:
        raise ValueError("OpenCL device enumeration changed.")
    devices = (c.c_void_p * count.value)()
    check(lib.clGetDeviceIDs(platform, CL_DEVICE_TYPE_GPU, count.value, devices, None))
    selected = devices[plan["device"]["platform_device_index"]]
    verify_device(lib, selected, plan["device"])
    bind_transfer(lib)
    result = execute_transfers(lib, selected, checkpoint)
    print(json.dumps({"methodology_version": "gpu-transfer-v1", "plan": plan,
                      "host_available_bytes": available, **result}))


if __name__ == "__main__":
    main()
