#!/usr/bin/env python

import io
from setuptools import find_packages, setup

setup(
    name='alertlib',
    version='1.0',
    author='Khan Academy',
    license='Proprietary',
    packages=find_packages(),
    scripts=['alert.py'],
    description='Khan Academy alert library',
    long_description='\n' + io.open('README.md', 'rb').read(),
)
