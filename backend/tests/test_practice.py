"""阶段 3-3：讯飞评测纯函数单元测试（离线可跑）。"""
import base64

import pytest

from app.core.errors import AppError
from app.services.practice import (
    _parse_ise_xml,
    build_audio_frame,
    build_auth_url,
    build_first_frame,
    parse_result,
)


def test_build_auth_url_contains_params():
    url = build_auth_url("ise-api.xfyun.cn", "/v2/open-ise", "apikey123", "apisecret123")
    assert url.startswith("wss://ise-api.xfyun.cn/v2/open-ise?")
    assert "authorization=" in url
    assert "host=ise-api.xfyun.cn" in url
    assert "date=" in url
    from urllib.parse import unquote

    auth_enc = url.split("authorization=")[1].split("&")[0]
    auth = base64.b64decode(unquote(auth_enc)).decode()
    assert 'api_key="apikey123"' in auth
    assert "hmac-sha256" in auth


def test_build_first_frame():
    f = build_first_frame("appid1", "hello world")
    assert f["common"]["app_id"] == "appid1"
    assert f["business"]["cmd"] == "ssb"
    assert f["business"]["ent"] == "en_vip"
    assert f["business"]["text"] == "hello world"
    assert f["data"]["status"] == 0


def test_build_audio_frame():
    f = build_audio_frame("appid1", "hi", aus=4, status=2, audio_b64="AAAA")
    assert f["business"]["cmd"] == "auw"
    assert f["business"]["aus"] == 4
    assert f["data"]["status"] == 2
    assert f["data"]["data"] == "AAAA"


def test_parse_ise_xml():
    xml = '<xml_result><read_sentence><rec_paper><read_chapter total_score="5.000000"></read_chapter></rec_paper></read_sentence></xml_result>'
    assert _parse_ise_xml(xml) == 5.0


def test_parse_result_success_from_base64_xml():
    xml = '<xml_result><read_chapter total_score="4.8"/></xml_result>'
    b64 = base64.b64encode(xml.encode()).decode()
    r = parse_result([{"code": 0, "message": "success", "data": {"data": b64}}])
    # 5 分制 4.8 → 百分制 96.0
    assert r["score"] == 96.0


def test_parse_result_extracts_word_items():
    xml = (
        '<xml_result><read_chapter total_score="4.0">'
        '<word beg_pos="0" end_pos="5" total_score="4.5">hello</word>'
        '<word beg_pos="6" end_pos="11" total_score="2.0">world</word>'
        '<word beg_pos="12" end_pos="18" total_score="3.5">friend</word>'
        '<word beg_pos="19" end_pos="23" total_score="1.0">bad</word>'
        '</read_chapter></xml_result>'
    )
    b64 = base64.b64encode(xml.encode()).decode()
    r = parse_result([{"code": 0, "data": {"data": b64}}])
    assert r["score"] == 80.0  # 4.0 * 20
    words = [w["word"] for w in r["word_items"]]
    assert words == ["bad", "world", "friend"]  # 低分优先，最多三处
    assert r["word_items"][0]["beg_pos"] == "19"


def test_parse_result_no_word_items_when_absent():
    xml = '<xml_result><read_chapter total_score="4.8"/></xml_result>'
    b64 = base64.b64encode(xml.encode()).decode()
    r = parse_result([{"code": 0, "data": {"data": b64}}])
    assert r["word_items"] == []


def test_parse_result_error():
    with pytest.raises(AppError) as e:
        parse_result([{"code": 10105, "message": "illegal access"}])
    assert e.value.code == "PRACTICE_EVAL_ERROR"
    assert "illegal access" not in str(e.value)


def test_parse_result_no_score():
    with pytest.raises(AppError):
        parse_result([{"code": 0, "data": {}}])


def test_mock_coach_practice_decisions():
    from app.services.coach import MockCoachProvider

    coach = MockCoachProvider()
    # 有效高分 → 继续原目标
    assert coach.decide({"practice": {"score": 85, "valid": True, "reference_sentence": "x"}}).action_type == "follow_up"
    # 有效低分 → 建议重试
    assert coach.decide({"practice": {"score": 40, "valid": True, "reference_sentence": "x"}}).action_type == "suggest_practice"
    # 无效/无分数 → 不可评估，继续原目标
    assert coach.decide({"practice": {"score": None, "valid": False, "reference_sentence": "x"}}).action_type == "follow_up"
