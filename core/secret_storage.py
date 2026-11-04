"""Windows account-bound encryption for persisted application credentials."""
import base64
import binascii
import ctypes
from ctypes import wintypes
import sys


class SecretStorageError(RuntimeError):
    """Safe to display: messages never contain a credential or ciphertext."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _DataBlob(len(data), buffer), buffer


def _crypt(data, context, *, decrypt=False):
    if sys.platform != "win32":
        raise SecretStorageError("Encrypted key storage is available on Windows. This key remains session-only.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    free = kernel.LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                          ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    operation.restype = wintypes.BOOL
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(("LRCPlus/credentials/v1/" + context).encode("utf-8"))
    output = _DataBlob()
    try:
        # Current-user scope; never CRYPTPROTECT_LOCAL_MACHINE. No OS prompt.
        if not operation(ctypes.byref(source), None if decrypt else "LRCPlus API key",
                         ctypes.byref(entropy), None, None, 0x1, ctypes.byref(output)):
            raise SecretStorageError("Could not decrypt the saved key for this Windows account. Enter it again."
                                     if decrypt else "Could not encrypt the key. It remains session-only.")
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(source_buffer, 0, len(source_buffer))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            free(ctypes.cast(output.data, ctypes.c_void_p))


def encrypt_secret(value, context):
    """Only an encrypted, versioned record may be passed to settings."""
    encrypted = _crypt(value.encode("utf-8"), context)
    return {"scheme": "windows-dpapi-v1", "data": base64.b64encode(encrypted).decode("ascii")}


def decrypt_secret(record, context):
    if record is None:
        return ""
    try:
        if not isinstance(record, dict) or record.get("scheme") != "windows-dpapi-v1":
            raise ValueError
        encrypted = base64.b64decode(record["data"], validate=True)
        if not encrypted:
            raise ValueError
        return _crypt(encrypted, context, decrypt=True).decode("utf-8")
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeError) as error:
        raise SecretStorageError("The saved encrypted key is invalid. Enter it again.") from error
