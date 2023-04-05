"""Python setup.py for configzen package"""
from setuptools import find_packages, setup

setup(
    name="configzen",
    version="0.0.0",
    description="PROJECT_DESCRIPTION",
    url="https://github.com/bswck/configzen/",
    long_description_content_type="text/markdown",
    author="bswck",
    packages=find_packages(exclude=["tests", ".github"]),
    entry_points={
        "console_scripts": ["configzen = configzen.__main__:main"]
    },
    extras_require={"test": ["pytest"]},
)
