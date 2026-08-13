import pytest
from victim.pagination import page_count, paginate


def test_paginate_first_page():
    result = paginate([1, 2, 3, 4, 5], page=1, per_page=3)
    assert result.items == [1, 2, 3]


def test_page_count_exact_multiple():
    assert page_count(10, 5) == 2


def test_page_count_of_empty():
    assert page_count(0, 5) == 0


def test_paginate_rejects_page_zero():
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page=0)


# WEAK: only the slice length is checked, so an off-by-one on the start
# offset (returning the wrong three items) still passes.
def test_paginate_second_page_has_right_length():
    result = paginate([1, 2, 3, 4, 5, 6], page=2, per_page=3)
    assert len(result.items) == 3


# WEAK: an inequality loose enough that a wrong rounding rule survives.
def test_page_count_rounds_up():
    assert page_count(11, 5) >= 2


# WEAK: asserts something about the *input*, not about the result.
def test_paginate_does_not_consume_input():
    source = [1, 2, 3, 4, 5]
    paginate(source, page=1, per_page=2)
    assert len(source) == 5


# WEAK: type-only assertion on a boolean property.
def test_page_has_next_is_bool():
    result = paginate([1, 2, 3], page=1, per_page=2)
    assert isinstance(result.has_next, bool)
