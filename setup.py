from setuptools import setup, find_packages
from setuptools.command.install import install
from setuptools.command.develop import develop
from setuptools.command.egg_info import egg_info
import os
import subprocess
import sys

if 'BUILT_FROM_SH' not in os.environ or not os.environ['BUILT_FROM_SH']:
    raise SystemError("pip can't check for a valid ZMQ install. Install by running the install.sh file instead.")

# Setup configuration for the package
setup(
    name='my_project',  # Change to your project name
    version='0.1',
    packages=find_packages(),
    install_requires=[
        # Add any other dependencies your project may require here
    ],
    entry_points={
        'console_scripts': [
            'server=server:main',  # Assumes you have a main function in server.py
            'client=client:main',  # Assumes you have a main function in client.py
        ],
    }
)
