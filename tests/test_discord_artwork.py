from io import BytesIO
import random
import struct
from types import SimpleNamespace
import zlib

import pytest
from PIL import Image, PngImagePlugin

from plugins.community.discord_rpc import plugin as rpc_module
from plugins.community.discord_rpc.plugin import DiscordRpcPlugin


def cover_bytes(size=(128, 64), *, mode="RGB", format="PNG", **kwargs):
    output = BytesIO()
    Image.new(mode, size, "red").save(output, format=format, **kwargs)
    return output.getvalue()


def oversized_pixel_header():
    # A valid PNG header declares too many pixels without allocating them.
    data = cover_bytes((1, 1))
    header = struct.pack(">II", 5001, 5000) + data[24:29]
    checksum = struct.pack(">I", zlib.crc32(b"IHDR" + header))
    return data[:16] + header + checksum + data[33:]


@pytest.mark.parametrize("format,extension", [("PNG", ".png"), ("JPEG", ".jpg")])
@pytest.mark.parametrize("size,expected", [
    ((128, 64), (128, 64)),
    ((512, 512), (512, 512)),
    ((2048, 1024), (512, 256)),
    ((100, 1600), (32, 512)),
    ((900, 1), (512, 1)),
])
def test_upload_cover_dimensions_and_small_image_passthrough(format, extension, size, expected):
    original = cover_bytes(size, format=format)
    prepared, suffix = DiscordRpcPlugin._prepare_cover_upload(original)
    assert suffix == extension
    assert len(prepared) <= rpc_module.LITTERBOX_MAX_UPLOAD_BYTES
    with Image.open(BytesIO(prepared)) as image:
        image.load()
        assert image.size == expected
        assert image.format == format
    if max(size) <= 512:
        assert prepared == original


def test_byte_limit_recompresses_small_cover_without_enlarging_it():
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "x" * rpc_module.LITTERBOX_MAX_UPLOAD_BYTES)
    original = cover_bytes(pnginfo=metadata)
    assert len(original) > rpc_module.LITTERBOX_MAX_UPLOAD_BYTES
    prepared, extension = DiscordRpcPlugin._prepare_cover_upload(original)
    assert extension == ".png"
    assert len(prepared) <= rpc_module.LITTERBOX_MAX_UPLOAD_BYTES
    with Image.open(BytesIO(prepared)) as image:
        assert image.size == (128, 64)


def test_resized_png_retains_transparency():
    output = BytesIO()
    Image.new("RGBA", (1024, 512), (10, 20, 30, 100)).save(output, format="PNG")
    prepared, extension = DiscordRpcPlugin._prepare_cover_upload(output.getvalue())
    assert extension == ".png"
    with Image.open(BytesIO(prepared)) as image:
        assert image.size == (512, 256)
        assert image.getpixel((0, 0))[3] == 100


def test_large_png_falls_back_to_jpeg_within_upload_limit():
    output = BytesIO()
    pixels = random.Random(0).randbytes(512 * 512 * 4)
    Image.frombytes("RGBA", (512, 512), pixels).save(output, format="PNG")
    original = output.getvalue()
    assert len(original) > rpc_module.LITTERBOX_MAX_UPLOAD_BYTES
    prepared, extension = DiscordRpcPlugin._prepare_cover_upload(original)
    assert extension == ".jpg"
    assert len(prepared) <= rpc_module.LITTERBOX_MAX_UPLOAD_BYTES
    with Image.open(BytesIO(prepared)) as image:
        image.load()
        assert image.format == "JPEG"
        assert image.size == (512, 512)
        assert image.mode == "RGB"


def test_resized_cmyk_jpeg_is_converted_to_rgb():
    prepared, extension = DiscordRpcPlugin._prepare_cover_upload(
        cover_bytes((1024, 512), mode="CMYK", format="JPEG")
    )
    assert extension == ".jpg"
    with Image.open(BytesIO(prepared)) as image:
        assert image.size == (512, 256)
        assert image.mode == "RGB"


def test_resizing_honors_exif_orientation():
    exif = Image.Exif()
    exif[274] = 6  # Rotate clockwise for display.
    original = cover_bytes((1000, 500), format="JPEG", exif=exif)
    prepared, _ = DiscordRpcPlugin._prepare_cover_upload(original)
    with Image.open(BytesIO(prepared)) as image:
        assert image.size == (256, 512)
        assert image.getexif().get(274) is None


def test_input_byte_limit_is_checked_before_opening_image(monkeypatch):
    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: pytest.fail("Opened oversized input"))
    with pytest.raises(ValueError, match="input limit"):
        DiscordRpcPlugin._prepare_cover_upload(b"x" * (rpc_module.LITTERBOX_MAX_INPUT_BYTES + 1))


def test_pixel_limit_is_checked_before_decoding(monkeypatch):
    original = oversized_pixel_header()
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load",
                        lambda *args, **kwargs: pytest.fail("Decoded excessive pixels"))
    with pytest.raises(ValueError, match="megapixel input limit"):
        DiscordRpcPlugin._prepare_cover_upload(original)


@pytest.fixture
def upload_plugin(monkeypatch):
    monkeypatch.setattr(DiscordRpcPlugin, "_load_persistent_cover_cache", lambda self: {})
    instance = DiscordRpcPlugin()
    instance.saved_caches = []
    monkeypatch.setattr(instance, "_save_persistent_cover_cache",
                        lambda: instance.saved_caches.append(dict(instance._persistent_cover_cache)))
    return instance


@pytest.mark.parametrize("format,extension,mime", [
    ("PNG", ".png", "image/png"), ("JPEG", ".jpg", "image/jpeg"),
])
def test_upload_uses_prepared_bytes_and_matching_mime_then_caches_url(
    upload_plugin, tmp_path, monkeypatch, format, extension, mime,
):
    track = tmp_path / "track.mp3"
    track.write_bytes(b"original audio and embedded artwork")
    original = cover_bytes((2048, 1024), format=format)
    # Deliberately incorrect tag MIME must not override the actual image format.
    monkeypatch.setattr(upload_plugin, "_extract_cover", lambda path: (original, ".wrong"))
    requests = []
    url = "https://litter.catbox.moe/cover" + extension

    def post(endpoint, **kwargs):
        requests.append((endpoint, kwargs))
        return SimpleNamespace(ok=True, text=url)

    monkeypatch.setattr(rpc_module.requests, "post", post)
    assert upload_plugin._upload_embedded_cover(track) == url
    endpoint, request = requests[0]
    filename, data, content_type = request["files"]["fileToUpload"]
    assert endpoint == rpc_module.LITTERBOX_UPLOAD_URL
    assert request["data"] == {"reqtype": "fileupload", "time": "12h"}
    assert filename == "lrcplus-cover" + extension
    assert content_type == mime
    assert len(data) <= rpc_module.LITTERBOX_MAX_UPLOAD_BYTES
    with Image.open(BytesIO(data)) as image:
        image.load()
        assert image.size == (512, 256)
    assert track.read_bytes() == b"original audio and embedded artwork"
    assert len(upload_plugin.saved_caches) == 1
    assert upload_plugin._persistent_cover_cache[upload_plugin._persistent_litterbox_key(track)]["url"] == url
    monkeypatch.setattr(upload_plugin, "_extract_cover", lambda path: pytest.fail("Re-extracted cached art"))
    assert upload_plugin._upload_embedded_cover(track) == url
    assert len(requests) == 1


@pytest.mark.parametrize("invalid", [
    b"not an image", cover_bytes()[:40], cover_bytes(format="GIF"),
    oversized_pixel_header(),
])
def test_rejected_artwork_is_not_uploaded_or_reprocessed(upload_plugin, tmp_path, monkeypatch, invalid):
    track = tmp_path / "track.mp3"
    track.touch()
    monkeypatch.setattr(upload_plugin, "_extract_cover", lambda path: (invalid, ".jpg"))
    monkeypatch.setattr(rpc_module.requests, "post", lambda *args, **kwargs: pytest.fail("Uploaded rejected art"))
    assert upload_plugin._upload_embedded_cover(track) is None
    monkeypatch.setattr(upload_plugin, "_extract_cover", lambda path: pytest.fail("Retried rejected art"))
    assert upload_plugin._upload_embedded_cover(track) is None
    assert upload_plugin.saved_caches == []


def test_image_still_over_upload_limit_is_skipped(upload_plugin, tmp_path, monkeypatch):
    track = tmp_path / "track.mp3"
    track.touch()
    original = cover_bytes()
    monkeypatch.setattr(upload_plugin, "_extract_cover", lambda path: (original, ".png"))
    # Force an unattainable limit to exercise the final rejection path.
    monkeypatch.setattr(rpc_module, "LITTERBOX_MAX_UPLOAD_BYTES", 1)
    monkeypatch.setattr(rpc_module.requests, "post", lambda *args, **kwargs: pytest.fail("Uploaded oversized art"))
    assert upload_plugin._upload_embedded_cover(track) is None
    assert upload_plugin.saved_caches == []


def test_valid_persistent_cache_bypasses_image_processing(upload_plugin, tmp_path, monkeypatch):
    track = tmp_path / "track.mp3"
    track.touch()
    url = "https://litter.catbox.moe/cached.jpg"
    upload_plugin._persistent_cover_cache[upload_plugin._persistent_litterbox_key(track)] = {
        "url": url, "expires_at": rpc_module.time.time() + 3600,
    }
    monkeypatch.setattr(upload_plugin, "_extract_cover", lambda path: pytest.fail("Extracted cached art"))
    monkeypatch.setattr(rpc_module.requests, "post", lambda *args, **kwargs: pytest.fail("Re-uploaded cached art"))
    assert upload_plugin._upload_embedded_cover(track) == url


def test_failed_upload_is_not_retried_for_every_presence_update(upload_plugin, tmp_path, monkeypatch):
    track = tmp_path / "track.mp3"
    track.touch()
    original = cover_bytes()
    monkeypatch.setattr(upload_plugin, "_extract_cover", lambda path: (original, ".png"))
    attempts = []

    def fail_upload(*args, **kwargs):
        attempts.append(True)
        raise rpc_module.requests.ConnectionError("Host unavailable")

    monkeypatch.setattr(rpc_module.requests, "post", fail_upload)
    assert upload_plugin._upload_embedded_cover(track) is None
    assert upload_plugin._upload_embedded_cover(track) is None
    assert len(attempts) == 1
