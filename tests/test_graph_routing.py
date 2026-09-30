from zarif.graph.nodes import route_after_verification


def test_routes_to_human_review_when_sufficient():
    state = {"sufficient": True, "attempt": 1, "max_attempts": 2}
    assert route_after_verification(state) == "human_review"


def test_routes_to_propose_edit_when_insufficient_and_attempts_left():
    state = {"sufficient": False, "attempt": 1, "max_attempts": 2}
    assert route_after_verification(state) == "propose_edit"


def test_routes_to_human_review_when_attempts_exhausted():
    state = {"sufficient": False, "attempt": 2, "max_attempts": 2}
    assert route_after_verification(state) == "human_review"
