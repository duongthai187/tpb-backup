from __future__ import annotations

import base64
from typing import Any, Dict

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.banks.base import SignatureVerifier
from app.banks.tpbank.settings import tpbank_settings

logger = structlog.get_logger()


class TPBankVerifier(SignatureVerifier):
    def __init__(self) -> None:
        self.public_key = None
        try:
            with open(tpbank_settings.public_key_file, "rb") as f:
                self.public_key = serialization.load_pem_public_key(f.read())
        except FileNotFoundError:
            logger.error(
                "tpbank.verifier.key_not_found",
                path=tpbank_settings.public_key_file,
            )
        except Exception as exc:
            logger.error(
                "tpbank.verifier.key_load_error",
                path=tpbank_settings.public_key_file,
                error=str(exc),
            )

    def _canonical_string(self, payload: Dict[str, Any]) -> bytes:
        source_app_id = str(payload.get("sourceAppId", ""))
        batch_id = str(payload.get("batchId", ""))
        timestamp = str(payload.get("timestamp", ""))
        return (source_app_id + batch_id + timestamp).encode("utf-8")

    def verify(self, payload: Dict[str, Any], signature: str) -> bool:
        if self.public_key is None:
            logger.warning("tpbank.verifier.no_public_key")
            return False
        try:
            sig_bytes = base64.b64decode(signature)
            self.public_key.verify(
                sig_bytes,
                self._canonical_string(payload),
                padding.PKCS1v15(),
                hashes.SHA512(),
            )
            return True
        except InvalidSignature:
            logger.warning("tpbank.verifier.invalid_signature", batch_id=payload.get("batchId"))
            return False
        except Exception as exc:
            logger.error("tpbank.verifier.verify_error", error=str(exc))
            return False
