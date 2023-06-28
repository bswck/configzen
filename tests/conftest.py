import functools
import pathlib

testpath = functools.partial(pathlib.Path(__file__).parent.joinpath)
