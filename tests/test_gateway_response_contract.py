from gateway.service import trader_rejected


def test_explicit_dict_rejection_is_rejected():
    assert trader_rejected({"ok": False, "reason": "outside zone"}) is True
    assert trader_rejected({"success": False}) is True


def test_success_contract_is_accepted():
    assert trader_rejected({"ok": True}) is False
    assert trader_rejected({"success": True}) is False


def test_empty_response_is_rejected():
    assert trader_rejected(None) is True
    assert trader_rejected({}) is True
