#!/bin/bash

./network_setup.sh
./pyzmq_setup.sh

export BUILT_FROM_SH=1
pip install -e .