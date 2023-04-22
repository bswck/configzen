import dataclasses
import random

import configzen as zen


@dataclasses.dataclass
class DictDataclass(zen.Dict):
    counter: int
    exitmsg: str


@dataclasses.dataclass
class TupleDataclass(zen.Tuple):
    test1: int
    test2: int


config = zen.Config(
    'test.json',
    dict=DictDataclass,
    tuple=TupleDataclass,
)
config.dict.counter += 1
config.dict.exitmsg = 'something happened!'
config['tuple'] = TupleDataclass(random.randint(1, 100), random.randint(1, 100))
config.save()
