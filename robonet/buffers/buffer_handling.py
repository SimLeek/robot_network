from robonet.buffers.buffer_objects import *  # needed for handling classes


# Packing Function
def pack_obj(obj):
    """Pack any object into a byte string for sending over a network."""
    class_name = obj.__class__.__name__
    obj_dict = obj.__dict__
    type_list = obj.__class__.type_list

    message = struct.pack('!I', len(class_name)) + class_name.encode('utf-8')

    for key, value in obj_dict.items():
        key_encoded = key.encode('utf-8')
        type_index = type_list.index(obj.__init__.__annotations__[key])  # Find type index

        # Pack the key and type index
        message += struct.pack('!I', len(key_encoded)) + key_encoded
        message += struct.pack('!I', type_index)

        # Pack the value using the corresponding class method
        message += obj.__class__.pack_type(value, type_index)

    return message


# Unpacking Function
def unpack_obj(message):
    """Unpack the message into the correct class based on the class name."""
    offset = 0
    class_name_len = struct.unpack_from('!I', message, offset)[0]
    offset += 4
    class_name = message[offset:offset + class_name_len].decode('utf-8')
    offset += class_name_len

    # Check if class exists in the global scope
    if class_name in globals():
        obj_class = globals()[class_name]
    else:
        raise TypeError(f"Unknown class name: {class_name}")

    obj_dict = {}
    while offset < len(message):
        key_len = struct.unpack_from('!I', message, offset)[0]
        offset += 4
        key = message[offset:offset + key_len].decode('utf-8')
        offset += key_len

        # Get the type index
        type_index = struct.unpack_from('!I', message, offset)[0]
        offset += 4

        # Unpack the value using the corresponding class method
        value, offset = obj_class.unpack_type(message, offset, type_index)

        obj_dict[key] = value

    # Create an instance of the object and set its attributes
    received_obj = obj_class.__new__(obj_class)
    received_obj.__dict__.update(obj_dict)

    return received_obj
