import socket

class block_network(socket.socket):
    def __init__(self, *args, **kwargs):
        raise Exception("Network calls blocked (tests)")


socket.socket = block_network
