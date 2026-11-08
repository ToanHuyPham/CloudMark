import ctypes as c
import unittest
from unittest.mock import patch, Mock

from cloudmark.gpu_transfer_worker import execute_transfers, verify_device
from cloudmark.gpu_transfer import run_gpu_transfer
from cloudmark.runner import JobContext, RunCancelled


class FakeOpenCL:
    def __init__(self, corrupt=False):
        self.buffers = {}
        self.released = []
        self.corrupt = corrupt

    def clCreateContext(self, *args):
        return 1

    def clCreateCommandQueue(self, *args):
        return 2

    def clCreateBuffer(self, context, flags, size, host, error):
        handle = 3 + len(self.buffers)
        self.buffers[handle] = bytearray(size)
        return handle

    def clEnqueueWriteBuffer(self, queue, handle, blocking, offset, size, source, *args):
        self.buffers[handle][:] = bytes(source)
        return 0

    def clEnqueueReadBuffer(self, queue, handle, blocking, offset, size, target, *args):
        value = bytes(self.buffers[handle])
        if self.corrupt:
            value = b"x" * size
        c.memmove(target, value, size)
        return 0

    def clEnqueueCopyBuffer(self, queue, source, target, *args):
        self.buffers[target][:] = self.buffers[source]
        return 0

    def clFinish(self, queue):
        return 0

    def clReleaseMemObject(self, handle):
        self.released.append(handle)
        return 0

    clReleaseContext = clReleaseMemObject
    clReleaseCommandQueue = clReleaseMemObject


class TransferWorkerTests(unittest.TestCase):
    def test_copies_verify_and_release_in_reverse_order(self):
        lib = FakeOpenCL()
        with patch("cloudmark.gpu_transfer_worker.BUFFER_BYTES", 64), patch(
            "cloudmark.gpu_transfer_worker.ITERATIONS", 2
        ):
            result = execute_transfers(lib, 99, lambda: None)
        self.assertTrue(result["cleanup_verified"])
        self.assertEqual(len(result["measurements"]), 3)
        self.assertTrue(all(item["integrity_verified"] for item in result["measurements"]))
        self.assertEqual(lib.released, [4, 3, 2, 1])

    def test_checksum_failure_and_cancel_release_resources(self):
        for corrupt in (False, True):
            lib = FakeOpenCL(corrupt)
            checkpoint = Mock(side_effect=[None, RunCancelled("cancel")]) if not corrupt else lambda: None
            with patch("cloudmark.gpu_transfer_worker.BUFFER_BYTES", 64), patch(
                "cloudmark.gpu_transfer_worker.ITERATIONS", 2
            ), self.assertRaises(RuntimeError):
                execute_transfers(lib, 99, checkpoint)
            self.assertEqual(lib.released, [4, 3, 2, 1])

    def test_identity_drift_stops_before_allocation(self):
        with patch("cloudmark.gpu_transfer_worker._info_text", return_value="different"), self.assertRaises(ValueError):
            verify_device(Mock(), 99, {"name": "expected"})

    def test_parent_confirmation_and_timeout_gate(self):
        context = JobContext("test", total_steps=1, timeout_seconds=91)
        with patch.object(context, "run_process") as process:
            for confirmation in (False, True):
                with self.assertRaises(ValueError):
                    run_gpu_transfer(0, confirm_load=confirmation, context=context)
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
