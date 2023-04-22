import asyncio
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


async def main():
    config = await zen.Config(
        'test.yaml',
        dispatcher=zen.SimpleDispatcher(dict=DictDataclass, tuple=TupleDataclass),
        asynchronous=True,
    )
    config.dict.counter += 1
    config.dict.exitmsg = 'something happened asynchronously!'
    config.tuple = TupleDataclass(random.randint(1, 100), random.randint(1, 100))
    await config.save()


asyncio.run(main())

