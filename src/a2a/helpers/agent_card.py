"""Utility functions for inspecting AgentCard instances."""

from a2a.types.a2a_pb2 import AgentCard


def display_agent_card(card: AgentCard) -> None:
    """Print a human-readable summary of an AgentCard to stdout.

    Args:
        card: The AgentCard proto message to display.
    """
    width = 52
    sep = '=' * width
    thin = '-' * width

    lines: list[str] = [sep, 'AgentCard'.center(width), sep]

    lines += [
        '--- General ---',
        f'Name        : {card.name}',
        f'Description : {card.description}',
        f'Version     : {card.version}',
    ]
    if card.documentation_url:
        lines.append(f'Docs URL    : {card.documentation_url}')
    if card.icon_url:
        lines.append(f'Icon URL    : {card.icon_url}')
    if card.HasField('provider'):
        url_suffix = f' ({card.provider.url})' if card.provider.url else ''
        lines.append(f'Provider    : {card.provider.organization}{url_suffix}')

    lines += ['', '--- Interfaces ---']
    for i, iface in enumerate(card.supported_interfaces):
        binding = f'{iface.protocol_binding} {iface.protocol_version}'.strip()
        parts = [
            p
            for p in [binding, f'tenant={iface.tenant}' if iface.tenant else '']
            if p
        ]
        suffix = f'  ({", ".join(parts)})' if parts else ''
        line = f'  [{i}] {iface.url}{suffix}'
        lines.append(line)

    lines += [
        '',
        '--- Capabilities ---',
        f'Streaming           : {card.capabilities.streaming}',
        f'Push notifications  : {card.capabilities.push_notifications}',
        f'Extended agent card : {card.capabilities.extended_agent_card}',
    ]

    lines += [
        '',
        '--- I/O Modes ---',
        f'Input  : {", ".join(card.default_input_modes) or "(none)"}',
        f'Output : {", ".join(card.default_output_modes) or "(none)"}',
    ]

    lines += ['', '--- Skills ---']
    if card.skills:
        for skill in card.skills:
            lines += [
                thin,
                f'  ID          : {skill.id}',
                f'  Name        : {skill.name}',
                f'  Description : {skill.description}',
                f'  Tags        : {", ".join(skill.tags) or "(none)"}',
            ]
            if skill.examples:
                for ex in skill.examples:
                    lines.append(f'  Example     : {ex}')
    else:
        lines.append('  (none)')

    lines.append(sep)
    print('\n'.join(lines))
