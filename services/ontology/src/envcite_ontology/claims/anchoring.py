"""錨定：標註的原文必須是該單元現行某版本列的文字。"""

import hashlib

from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.corpus_ref import UnknownProvision, provision_id
from envcite_ontology.db import Conn


def provision(conn: Conn, law: object, path: object) -> int:
    """法規與路徑對應的單元 id；找不到則以標註錯誤拒絕。"""
    try:
        return provision_id(conn, law, path)
    except UnknownProvision as e:
        raise AnnotationError(str(e)) from e


def anchor(conn: Conn, what: str, ident: str, law: str, path: str, text: str) -> tuple[int, bytes]:
    """（單元，內容雜湊）：附的原文雜湊必須是該單元現行某版本列的雜湊。"""
    provision_id = provision(conn, law, path)
    digest = hashlib.sha256(text.encode()).digest()
    current = conn.execute(
        "SELECT 1 FROM corpus.provision_version"
        " WHERE provision_id = %s AND content_sha256 = %s AND superseded_at IS NULL",
        (provision_id, digest),
    ).fetchone()
    if current is None:
        raise AnnotationError(f"{what} {ident} 錨定的文字不是 {law} {path} 現行任何版本的文字")
    return provision_id, digest
