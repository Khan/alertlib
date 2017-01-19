#!/usr/bin/env python

import io
from setuptools import find_packages, setup

setup(
    name='alertlib',
    version='1.0',
    author='Khan Academy',
    license='Proprietary',
    packages=find_packages(),
    install_requires=['six'],
    scripts=['alert.py'],
    description='Khan Academy alert library',
    long_description='\n' + io.open('README.md', encoding='utf-8').read(),
)
