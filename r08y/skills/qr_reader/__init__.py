"""QR Reader skill — detect and decode QR codes and barcodes.

Continuously scans the camera feed for QR codes (throttled) and announces
new codes. Also available on-demand via voice command.

Events emitted: qr.decoded
"""

from typing import List, Optional

import cv2
import numpy as np

from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._detector = None
        self._last_decoded: Optional[str] = None
        self._scanning = False

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="qr_reader",
            version="1.0.0",
            description="Detect and decode QR codes and barcodes",
            requires={"camera"},
            pipeline_hooks=[PipelineHook.VISION_FRAME],
            vision_frame_interval=0.5,
            commands=[
                VoiceCommand(triggers=["scan qr", "read qr", "scan barcode", "read code"], handler="cmd_scan"),
                VoiceCommand(triggers=["stop scanning", "stop qr"], handler="cmd_stop"),
                VoiceCommand(triggers=["last qr", "what was the code"], handler="cmd_last"),
            ],
            emits_events=["qr.decoded"],
        )

    async def start(self):
        await super().start()
        try:
            self._detector = cv2.QRCodeDetector()
            self.ctx.logger.info("QR code detector initialized")
        except Exception:
            self.ctx.logger.warning("QR code detector not available in this OpenCV build")

    async def on_vision_frame(self, frame: np.ndarray) -> None:
        if not self._scanning or self._detector is None:
            return

        try:
            data, bbox, _ = self._detector.detectAndDecode(frame)
            if data and data != self._last_decoded:
                self._last_decoded = data
                await self.ctx.event_bus.emit("qr.decoded", source=self.name, data=data)
                self.ctx.logger.info("QR decoded: %s", data[:100])
                if self.ctx.tts.available:
                    truncated = data[:80] if len(data) > 80 else data
                    await self.ctx.tts.speak(f"QR code says: {truncated}")
        except Exception:
            pass

    async def cmd_scan(self, text: str, user_name: str) -> Optional[str]:
        if self._detector is None:
            return "QR scanning isn't available in this OpenCV build."
        self._scanning = True
        self._last_decoded = None
        return "Scanning for QR codes. Hold a code up to the camera."

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        self._scanning = False
        return "QR scanning stopped."

    async def cmd_last(self, text: str, user_name: str) -> Optional[str]:
        if self._last_decoded:
            return f"The last QR code said: {self._last_decoded[:100]}"
        return "I haven't scanned any QR codes yet."
