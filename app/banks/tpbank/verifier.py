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
        self.public_keys: Dict[str, Any] = {
            "prod": None,
            "uat": None,
        }

        for env_name, path in (
            ("prod", tpbank_settings.public_key_file),
            ("uat", tpbank_settings.uat_public_key_file),
        ):
            try:
                with open(path, "rb") as f:
                    self.public_keys[env_name] = serialization.load_pem_public_key(f.read())
                logger.info("tpbank.verifier.key_loaded", env=env_name, path=path)
            except FileNotFoundError:
                logger.error(
                    "tpbank.verifier.key_not_found",
                    env=env_name,
                    path=path,
                )
            except Exception as exc:
                logger.error(
                    "tpbank.verifier.key_load_error",
                    env=env_name,
                    path=path,
                    error=str(exc),
                )

    def _canonical_string(self, payload: Dict[str, Any]) -> bytes:
        source_app_id = str(payload.get("sourceAppId", ""))
        batch_id = str(payload.get("batchId", ""))
        timestamp = str(payload.get("timestamp", ""))
        return (source_app_id + batch_id + timestamp).encode("utf-8")

    def verify(self, payload: Dict[str, Any], signature: str, *, is_uat: bool = False) -> bool:
        key = self.public_keys["uat" if is_uat else "prod"]
        if key is None:
            logger.warning(
                "tpbank.verifier.no_public_key",
                env="uat" if is_uat else "prod",
            )
            return False
        try:
            sig_bytes = base64.b64decode(signature)
            key.verify(
                sig_bytes,
                self._canonical_string(payload),
                padding.PKCS1v15(),
                hashes.SHA512(),
            )
            return True
        except InvalidSignature:
            logger.warning(
                "tpbank.verifier.invalid_signature",
                batch_id=payload.get("batchId"),
                env="uat" if is_uat else "prod",
            )
            return False
        except Exception as exc:
            logger.error(
                "tpbank.verifier.verify_error",
                env="uat" if is_uat else "prod",
                error=str(exc),
            )
            return False
