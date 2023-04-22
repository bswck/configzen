import dataclasses

import configzen as zen


@dataclasses.dataclass
class Item(zen.Dataclass):
    counter: int
    exitmsg: str


config = zen.Config(
    'test.yaml',
    item=Item
)
config.item.counter += 1
config.item.exitmsg = 'something happened!'
config.save()

