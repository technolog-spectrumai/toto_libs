"""
Hardcoded ffmpeg/ffprobe argv builders.
Each function returns list[str] — never a shell string.
Progress flags (-progress pipe:1 -nostats) are injected by the runner
just before the output path so builders stay self-contained.
"""

_QUALITY_CRF = {
    "tiny":    "35",
    "small":   "28",
    "medium":  "23",
    "high":    "18",
    "archive": "12",
}


def build_probe(input_path: str) -> list[str]:
    return [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        input_path,
    ]


def build_compress(input_path: str, output_path: str, quality: str = "medium") -> list[str]:
    crf = _QUALITY_CRF.get(quality, _QUALITY_CRF["medium"])
    return [
        "ffmpeg", "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", crf,
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]


def build_resize(
    input_path: str,
    output_path: str,
    width: int = -2,
    height: int = -2,
) -> list[str]:
    # -2 means "auto, keep divisible by 2" — ffmpeg computes it from the other dimension.
    scale = f"{width}:{height}"
    return [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", f"scale={scale}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        output_path,
    ]


def build_cut(
    input_path: str,
    output_path: str,
    start_time: str,
    end_time: str,
) -> list[str]:
    return [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ss", start_time,
        "-to", end_time,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        output_path,
    ]


def build_extract_mp3(
    input_path: str,
    output_path: str,
    bitrate: str = "192k",
) -> list[str]:
    return [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-ab", bitrate,
        output_path,
    ]


def build_thumbnail(
    input_path: str,
    output_path: str,
    at_time: str = "00:00:01",
) -> list[str]:
    return [
        "ffmpeg", "-y",
        "-ss", at_time,
        "-i", input_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]


def build_gif_palette(
    input_path: str,
    palette_path: str,
    start_time: str = "00:00:00",
    duration: str = "5",
    fps: int = 12,
    width: int = 480,
) -> list[str]:
    vf = f"fps={fps},scale={width}:-1:flags=lanczos,palettegen"
    return [
        "ffmpeg", "-y",
        "-ss", start_time,
        "-t", str(duration),
        "-i", input_path,
        "-vf", vf,
        palette_path,
    ]


def build_gif(
    input_path: str,
    palette_path: str,
    output_path: str,
    start_time: str = "00:00:00",
    duration: str = "5",
    fps: int = 12,
    width: int = 480,
) -> list[str]:
    vf = f"fps={fps},scale={width}:-1:flags=lanczos [x]; [x][1:v] paletteuse"
    return [
        "ffmpeg", "-y",
        "-ss", start_time,
        "-t", str(duration),
        "-i", input_path,
        "-i", palette_path,
        "-lavfi", vf,
        output_path,
    ]


def build_concat(
    concat_list_path: str,
    output_path: str,
    reencode: bool = False,
) -> list[str]:
    argv = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
    ]
    if reencode:
        argv += ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-c:a", "aac"]
    else:
        argv += ["-c", "copy"]
    argv.append(output_path)
    return argv
