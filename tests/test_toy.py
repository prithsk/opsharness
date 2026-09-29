import unittest

from opsharness.testing import WorldContract
from opsharness.toy import ToyEnv


class TestToyWorld(WorldContract, unittest.TestCase):
    world = ToyEnv
