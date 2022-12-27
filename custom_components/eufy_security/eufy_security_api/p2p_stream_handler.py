""" Module to handle go2rtc interactions """
from __future__ import annotations

import asyncio
import logging
import socket
from time import sleep
import traceback

_LOGGER: logging.Logger = logging.getLogger(__package__)

FFMPEG_COMMAND = [
    "-analyzeduration",
    "{duration}",
    "-f",
    "{video_codec}",
    "-i",
    # "-",
    "tcp://localhost:{port}",
    "-vcodec",
    "copy",
]
FFMPEG_OPTIONS = (
    " -hls_init_time 0"
    " -hls_time 1"
    " -hls_segment_type mpegts"
    " -hls_playlist_type event "
    " -hls_list_size 0"
    " -preset ultrafast"
    " -tune zerolatency"
    " -g 15"
    " -sc_threshold 0"
    " -fflags genpts+nobuffer+flush_packets"
    " -loglevel debug"
)


class P2PStreamHandler:
    """Class to manage external stream provider and byte based ffmpeg streaming"""

    def __init__(self, camera) -> None:
        self.camera = camera

        self.port = None
        self.loop = None
        self.ffmpeg = None

    async def start_ffmpeg(self, duration):
        """start ffmpeg process"""
        self.loop = asyncio.get_running_loop()
        command = FFMPEG_COMMAND.copy()
        input_index = command.index("-i")
        command[input_index - 3] = str(duration)
        codec = "hevc" if self.camera.codec == "h265" else self.camera.codec
        command[input_index - 1] = codec
        command[input_index + 1] = command[input_index + 1].replace("{port}", str(self.port))
        options = FFMPEG_OPTIONS + " -report"
        stream_url = f"-f rtsp -rtsp_transport tcp {self.camera.stream_url}"
        await self.ffmpeg.open(
            cmd=command,
            input_source=None,
            extra_cmd=options,
            output=stream_url,
            stderr_pipe=False,
            stdout_pipe=False,
        )
        _LOGGER.debug(f"start_ffmpeg - stream_url {stream_url} command {command} options {options}")

    @property
    def ffmpeg_available(self) -> bool:
        """True if ffmpeg exists and running"""
        return self.ffmpeg is not None and self.ffmpeg.is_running is True

    def setup(self, ffmpeg, port_ready_future):
        """Setup the handler"""
        self.ffmpeg = ffmpeg
        self.port = None
        empty_queue_counter = 0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("localhost", 0))
            self.port = sock.getsockname()[1]
            port_ready_future.set_result(True)
            # self._set_remote_config()
            _LOGGER.debug(f"p2p 1 - waiting")
            sock.listen()
            client_socket, _ = sock.accept()
            _LOGGER.debug(f"p2p 1 - arrived")
            client_socket.setblocking(False)
            try:
                with client_socket:
                    while empty_queue_counter < 10 and self.ffmpeg_available:
                        _LOGGER.debug(f"p2p 5 - q size: {self.camera.video_queue.qsize()} - empty {empty_queue_counter}")
                        if self.camera.video_queue.empty():
                            empty_queue_counter = empty_queue_counter + 1
                        else:
                            empty_queue_counter = 0
                            while not self.camera.video_queue.empty():
                                client_socket.sendall(bytearray(self.camera.video_queue.get()))
                        sleep(500 / 1000)
                _LOGGER.debug(f"p2p 6")
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.debug(f"Exception %s - traceback: %s", ex, traceback.format_exc())
        self.port = None
        self.ffmpeg = None
        asyncio.run_coroutine_threadsafe(self.stop(), self.loop).result()
        _LOGGER.debug(f"p2p 7")

    async def stop(self):
        """kill ffmpeg process"""
        if self.ffmpeg is not None:
            await self.ffmpeg.close(timeout=1)