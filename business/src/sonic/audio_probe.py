"""Bounded audio inspection, executed in a subprocess with a parent timeout.

Never fetch URLs. Select the demuxer based on an already checked signature.
"""
import json
import sys
import av


def inspect(path, demuxer):
    with av.open(path, format=demuxer, options={'protocol_whitelist': 'file', 'err_detect': 'explode', 'enable_drefs': '0'}) as container:
        if len(container.streams) != 1 or len(container.streams.audio) != 1:
            raise ValueError('Exactly one audio stream is required')
        stream = container.streams.audio[0]
        codec = stream.codec_context.name
        if (demuxer == 'wav' and codec != 'pcm_s16le') or (demuxer != 'wav' and codec != 'aac'):
            raise ValueError('Unsupported codec')
        rate = stream.codec_context.sample_rate
        channels = stream.codec_context.channels
        if not 8000 <= rate <= 48000 or channels not in (1, 2):
            raise ValueError('Unsupported sample rate or channels')
        samples = 0
        for frame in container.decode(stream):
            if frame.sample_rate != rate or len(frame.layout.channels) != channels:
                raise ValueError('Changing audio format is unsupported')
            samples += frame.samples
            if samples > rate * 600:
                raise ValueError('Audio exceeds 600 seconds')
        if samples == 0:
            raise ValueError('Audio contains no samples')
        return {'codec': codec, 'sample_rate': rate, 'channels': channels,
                'duration_ms': max(1, round(samples * 1000 / rate))}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect(sys.argv[1], sys.argv[2])))
    except Exception:
        print('{"error":"Invalid or unsupported audio"}')
        sys.exit(1)
