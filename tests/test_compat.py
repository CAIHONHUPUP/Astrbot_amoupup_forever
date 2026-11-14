# -*- coding: utf-8 -*-
from compat import load, try_attr

class Thing:
    a = 1

def test_load_module_and_attr():
    import os
    assert load("does.not.exist", "os", "path") is os.path
    assert load("does.not.exist", "os", "nope", "d") == "d"

def test_try_attr():
    assert try_attr(Thing, "a") == 1
    assert try_attr(None, "a", 2) == 2
