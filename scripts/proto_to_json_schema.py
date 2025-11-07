"""Convert protobuf to JSON Schema.

This script converts protobuf descriptors from generated Python files to JSON Schema format,
which can then be used with datamodel-codegen to generate Pydantic models.

The conversion maintains the same structure as the original a2a.json schema,
particularly for discriminated unions (oneof fields).
"""

import json
import sys

from pathlib import Path


def proto_type_to_json_schema_type(field) -> dict:
    """Convert protobuf field type to JSON Schema type."""
    from google.protobuf.descriptor import FieldDescriptor
    
    type_map = {
        FieldDescriptor.TYPE_DOUBLE: {'type': 'number'},
        FieldDescriptor.TYPE_FLOAT: {'type': 'number'},
        FieldDescriptor.TYPE_INT64: {'type': 'integer'},
        FieldDescriptor.TYPE_UINT64: {'type': 'integer'},
        FieldDescriptor.TYPE_INT32: {'type': 'integer'},
        FieldDescriptor.TYPE_BOOL: {'type': 'boolean'},
        FieldDescriptor.TYPE_STRING: {'type': 'string'},
        FieldDescriptor.TYPE_BYTES: {'type': 'string', 'format': 'byte'},
        FieldDescriptor.TYPE_UINT32: {'type': 'integer'},
    }
    
    if field.type == FieldDescriptor.TYPE_MESSAGE:
        full_name = field.message_type.full_name
        if 'Struct' in full_name:
            return {'type': 'object'}
        elif 'Timestamp' in full_name:
            return {'type': 'string', 'format': 'date-time'}
        else:
            return {'$ref': f'#/definitions/{field.message_type.name}'}
    elif field.type == FieldDescriptor.TYPE_ENUM:
        return {'$ref': f'#/definitions/{field.enum_type.name}'}
    else:
        return type_map.get(field.type, {'type': 'string'})


def message_to_json_schema(msg_desc, definitions: dict) -> dict:
    """Convert a protobuf message to JSON Schema."""
    from google.protobuf.descriptor import FieldDescriptor
    
    # Reserved Python keywords that need renaming
    reserved_keywords = {'list', 'dict', 'set', 'type', 'filter', 'map', 'id', 'input', 'format'}
    
    # Check for oneof fields - convert to discriminated union
    oneofs = [o for o in msg_desc.oneofs if not o.name.startswith('_')]
    
    if len(oneofs) == 1:
        oneof = oneofs[0]
        oneof_field_count = len(oneof.fields)
        total_fields = len(msg_desc.fields)
        
        # If >50% of fields are in the oneof, it's likely a discriminated union
        if oneof_field_count >= 2 and oneof_field_count >= total_fields * 0.5:
            # This is a discriminated union
            non_oneof_fields = [f for f in msg_desc.fields if f.containing_oneof != oneof]
            
            # Create variant schemas
            variants = []
            for field in oneof.fields:
                variant_name = f'{field.name.title().replace("_", "")}{msg_desc.name}'
                if variant_name.endswith('Part'):
                    variant_name = f'{field.name.title()}{msg_desc.name}'
                
                variant_schema = {
                    'type': 'object',
                    'title': variant_name,
                    'properties': {
                        'kind': {
                            'type': 'string',
                            'const': field.name.replace('_', '-'),
                            'default': field.name.replace('_', '-')
                        }
                    },
                    'required': ['kind']
                }
                
                # Add the variant-specific property
                field_name = field.name
                if field_name in reserved_keywords:
                    field_name = f'{field_name}_'
                variant_schema['properties'][field_name] = proto_type_to_json_schema_type(field)
                variant_schema['required'].append(field_name)
                
                # Add shared properties
                for shared_field in non_oneof_fields:
                    shared_field_name = shared_field.name
                    if shared_field_name in reserved_keywords:
                        shared_field_name = f'{shared_field_name}_'
                    prop_schema = proto_type_to_json_schema_type(shared_field)
                    variant_schema['properties'][shared_field_name] = prop_schema
                
                definitions[variant_name] = variant_schema
                variants.append({'$ref': f'#/definitions/{variant_name}'})
            
            # Return a oneOf schema
            return {
                'title': msg_desc.name,
                'oneOf': variants
            }
    
    # Standard message processing
    schema = {
        'type': 'object',
        'title': msg_desc.name,
        'properties': {},
        'required': []
    }
    
    for field in msg_desc.fields:
        field_name = field.name
        if field_name in reserved_keywords:
            field_name = f'{field_name}_'
            
        prop_schema = proto_type_to_json_schema_type(field)
        
        # Handle repeated fields
        if field.label == FieldDescriptor.LABEL_REPEATED:
            prop_schema = {
                'type': 'array',
                'items': prop_schema
            }
        
        schema['properties'][field_name] = prop_schema
        
        # Proto3 fields are all optional by default
        # Only mark as required if it's not in a oneof and not optional
        if not field.containing_oneof and field.label != FieldDescriptor.LABEL_OPTIONAL:
            # In proto3, all singular fields are implicitly optional
            pass
    
    return schema


def enum_to_json_schema(enum_desc) -> dict:
    """Convert a protobuf enum to JSON Schema."""
    return {
        'type': 'string',
        'title': enum_desc.name,
        'enum': [v.name.lower() for v in enum_desc.values]
    }


def convert_proto_to_json_schema(output_file: str) -> None:
    """Convert protobuf descriptors to JSON Schema."""
    # Import the generated protobuf module
    from a2a.grpc import a2a_pb2
    
    # Get the file descriptor
    file_descriptor = a2a_pb2.DESCRIPTOR
    
    # Convert to JSON Schema
    schema = {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'A2A Protocol',
        'definitions': {}
    }
    
    # Get all message types and enums from the module
    message_types = []
    enum_types = []
    
    for name in dir(a2a_pb2):
        obj = getattr(a2a_pb2, name)
        # Check if it's a message class
        if hasattr(obj, 'DESCRIPTOR') and hasattr(obj.DESCRIPTOR, 'full_name'):
            full_name = obj.DESCRIPTOR.full_name
            # Skip google types and only include a2a types
            if full_name.startswith('a2a.v1.'):
                # Check if it's an enum
                if hasattr(obj.DESCRIPTOR, 'values'):
                    enum_types.append(obj.DESCRIPTOR)
                # Check if it's a message
                elif hasattr(obj.DESCRIPTOR, 'fields'):
                    message_types.append(obj.DESCRIPTOR)
    
    # Convert enums
    for enum_desc in enum_types:
        schema['definitions'][enum_desc.name] = enum_to_json_schema(enum_desc)
    
    # Convert messages
    for msg_desc in message_types:
        msg_schema = message_to_json_schema(msg_desc, schema['definitions'])
        schema['definitions'][msg_desc.name] = msg_schema
    
    # Write output
    with open(output_file, 'w') as f:
        json.dump(schema, f, indent=2)
    
    print(f'Successfully converted proto to JSON Schema')
    print(f'Output: {output_file}')
    print(f'Definitions: {len(schema["definitions"])}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python proto_to_json_schema.py <output-json>')
        sys.exit(1)
    
    convert_proto_to_json_schema(sys.argv[1])
