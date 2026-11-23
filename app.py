import asyncio
import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

app = FastAPI(title="ESP32 YouTube Audio Proxy")

COOKIE_PATH = "/tmp/cookies.txt"


def prepare_cookies() -> str | None:
    cookies_content = os.environ.get("YT_COOKIES", "").strip()
    if not cookies_content:
        return None
    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
        f.write(cookies_content + "\n")
    return COOKIE_PATH


async def resolve_youtube_audio(query: str) -> tuple[str, str]:
    target = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--remote-components", "ejs:github",
        "--extractor-args", "youtube:player_client=default,web_safari,tv",
        "-f", "bestaudio/best",
        "--print", "title",
        "--print", "url",
    ]
    cookie_file = prepare_cookies()
    if cookie_file:
        cmd.extend(["--cookies", cookie_file])
    cmd.append(target)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="ignore").strip()
        raise HTTPException(status_code=500, detail=f"yt-dlp error: {err_msg}")

    lines = [line.strip() for line in stdout.decode("utf-8", errors="ignore").splitlines() if line.strip()]
    if len(lines) < 2:
        raise HTTPException(status_code=404, detail="Không tìm thấy luồng âm thanh.")

    return lines[0], lines[1]


@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "cookies_loaded": bool(os.environ.get("YT_COOKIES", "").strip()),
    }


@app.get("/info")
async def get_song_info(q: str = Query(...)):
    title, _ = await resolve_youtube_audio(q)
    return {"query": q, "title": title}


@app.get("/stream")
async def stream_youtube_mp3(q: str = Query(...)):
    title, audio_url = await resolve_youtube_audio(q)

    ffmpeg_cmd = [
        "ffmpeg",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", audio_url,
        "-vn",
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "128k",
        "-f", "mp3",
        "pipe:1",
    ]

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def audio_generator():
        try:
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(16384)
                if not chunk:
                    break
                yield chunk
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    headers = {
        "X-Song-Title": title.encode("ascii", "ignore").decode("ascii"),
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(audio_generator(), media_type="audio/mpeg", headers=headers)