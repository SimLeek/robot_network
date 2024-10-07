from setuptools import setup, find_packages
import os

if 'BUILT_FROM_SH' not in os.environ or not os.environ['BUILT_FROM_SH']:
    raise SystemError("pip can't check for a valid ZMQ install. Install by running the install.sh file instead.")

# Setup configuration for the package
setup(
    name='robotnet',  # Change to your project name
    version='0.1',
    packages=find_packages(),
    install_requires=[
        'numpy',
        'opencv-python~=4.10.0.84',
        'Cython',
        'PyV4L2Cam @ git+https://github.com/simleek/PyV4L2Cam.git',
        # pyzmq... but dn't install from here
    ],
    entry_points={
        'console_scripts': [
            'server=server:main',  # Assumes you have a main function in server.py
            'client=client:main',  # Assumes you have a main function in client.py
        ],
    }
)
