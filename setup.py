from setuptools import setup, find_packages
import os
import subprocess
import sys
import distro
def in_venv():
    return sys.prefix != sys.base_prefix

def is_arch():
    return 'Arch' in distro.name()

if is_arch() and not in_venv():
    raise SystemError("Cannot pip install ZMQ with custom build options on a system level")

def build_pyzmq():
    # Check if the required environment variables are set
    if 'ZMQ_PREFIX' not in os.environ:
        os.environ['ZMQ_DRAFT_API'] = 'bundled'

    # Set up draft API environment variables
    os.environ['ZMQ_DRAFT_API'] = '1'

    # Optional: Set rpath if needed (depends on installation)
    if os.environ['ZMQ_DRAFT_API']!='bundled':
        os.environ['LDFLAGS'] = f"{os.environ.get('LDFLAGS', '')} -Wl,-rpath,{os.environ['ZMQ_PREFIX']}/lib"

    # Install pyzmq from source with draft support
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-v', 'pyzmq', '--no-binary', 'pyzmq'])
    except subprocess.CalledProcessError as e:
        print(f"Failed to install pyzmq: {e}")
        sys.exit(1)

def uninstall_zmq():
    import subprocess
    subprocess.check_call(["python", "-m", "pip", "uninstall", "-y", 'zmq'])

try:
    import zmq
    if not zmq.has('draft'):
        uninstall_zmq()
        build_pyzmq()
except ImportError:
    build_pyzmq()

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
    },
)

