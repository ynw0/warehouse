"""抽取结果的对象语义校验：成品线不能被写成连接器。"""

from __future__ import annotations

_CABLE_HEADS = ("连接线", "电缆", "排线", "线缆")
_CONNECTOR_HEADS = ("接头", "连接器", "插头", "插座")
_INTERFACE_MARKERS = ("HDMI", "VGA", "USB", "TYPE-C", "TYPEC", "DVI", "RJ45")


class ObjectSemanticsError(ValueError):
    """抽取的对象无法通过程序校验。"""


def _fold(text: str) -> str:
    return text.upper().replace(" ", "").replace("－", "-").replace("—", "-")


def is_cable_object_head(object_head: str) -> bool:
    head = object_head.strip()
    if not head or any(head.endswith(token) for token in _CONNECTOR_HEADS):
        return False
    if any(token in head for token in _CABLE_HEADS):
        return True
    folded = _fold(head)
    return head.endswith("线") and any(marker in folded for marker in _INTERFACE_MARKERS)


def validate_extracted_object(object_head: str, object_form: str) -> None:
    """线、排线、连接线、成品接口线不得抽成连接器。"""

    head = object_head.strip()
    if not head:
        raise ObjectSemanticsError("对象本体为空")
    form = object_form.strip()
    if is_cable_object_head(head) and any(token in form for token in _CONNECTOR_HEADS):
        raise ObjectSemanticsError(f"对象本体为“{head}”，对象形态不得为连接器")
