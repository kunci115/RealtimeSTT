#!/usr/bin/env python3
"""
Test client to verify the server rejection system works correctly.
Tests different rejection policies and thresholds.
"""

import asyncio
import websockets
import numpy as np
import json
import struct
import time
from datetime import datetime


class RejectionTestClient:
    def __init__(self,
                 control_url="ws://localhost:8011",
                 data_url="ws://localhost:8012",
                 sample_rate=16000):

        self.control_url = control_url
        self.data_url = data_url
        self.sample_rate = sample_rate

        # State
        self.control_ws = None
        self.data_ws = None
        self.chunks_sent = 0
        self.connection_closed = False
        self.rejection_received = False

    def generate_test_audio(self, duration_ms=100):
        """Generate synthetic audio data"""
        num_samples = int(self.sample_rate * duration_ms / 1000)
        t = np.linspace(0, duration_ms / 1000, num_samples, False)
        frequency = 440  # A4 note
        audio = np.sin(2 * np.pi * frequency * t) * 0.3
        audio_int16 = (audio * 32767).astype(np.int16)
        return audio_int16.tobytes()

    def calculate_checksum(self, audio_data):
        """Calculate checksum for data verification"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        checksum = int(np.sum(audio_array, dtype=np.int64)) & 0xFFFFFFFF
        return checksum

    async def connect(self):
        """Connect to WebSocket servers"""
        try:
            self.control_ws = await websockets.connect(self.control_url)
            self.data_ws = await websockets.connect(self.data_url)
            print(f"[OK] Connected to servers")
            return True
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")
            return False

    async def handle_data_messages(self):
        """Handle server responses including rejection messages"""
        try:
            async for message in self.data_ws:
                data = json.loads(message)
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                if data.get('type') == 'error' and data.get('error') == 'data_corruption':
                    print(f"[{timestamp}] [REJECTED] {data.get('message', 'Connection rejected')}")
                    self.rejection_received = True
                    if data.get('action') == 'disconnect':
                        print(f"[{timestamp}] [DISCONNECT] Server is closing connection")
                        break
                else:
                    print(f"[{timestamp}] [SERVER] {data}")

        except websockets.exceptions.ConnectionClosed as e:
            print(f"[INFO] Connection closed: {e}")
            self.connection_closed = True
        except Exception as e:
            print(f"[ERROR] Error handling messages: {e}")

    async def send_corrupted_chunk(self, corruption_type="wrong_checksum"):
        """Send audio chunk with intentionally corrupted verification data"""
        if not self.data_ws:
            return False

        try:
            # Generate original audio
            original_audio = self.generate_test_audio(duration_ms=200)
            actual_checksum = self.calculate_checksum(original_audio)
            actual_length = len(np.frombuffer(original_audio, dtype=np.int16))

            # Create corrupted metadata
            if corruption_type == "wrong_checksum":
                corrupt_checksum = (actual_checksum + 12345) & 0xFFFFFFFF
                metadata = {
                    'sampleRate': self.sample_rate,
                    'dataLength': actual_length,
                    'checksum': corrupt_checksum,  # Wrong checksum
                    'timestamp': int(time.time() * 1000),
                    'server_sent_to_stt': True
                }
                audio_to_send = original_audio

            elif corruption_type == "wrong_length":
                metadata = {
                    'sampleRate': self.sample_rate,
                    'dataLength': actual_length + 100,  # Wrong length
                    'checksum': actual_checksum,
                    'timestamp': int(time.time() * 1000),
                    'server_sent_to_stt': True
                }
                audio_to_send = original_audio

            # Encode and send
            metadata_json = json.dumps(metadata)
            metadata_bytes = metadata_json.encode('utf-8')
            metadata_length = struct.pack('<I', len(metadata_bytes))

            message = metadata_length + metadata_bytes + audio_to_send
            await self.data_ws.send(message)

            self.chunks_sent += 1
            print(f"[SEND] Sent corrupted chunk {self.chunks_sent} ({corruption_type})")
            return True

        except websockets.exceptions.ConnectionClosed:
            print("[INFO] Connection closed while sending")
            self.connection_closed = True
            return False
        except Exception as e:
            print(f"[ERROR] Error sending corrupted chunk: {e}")
            return False

    async def send_valid_chunk(self):
        """Send a valid audio chunk"""
        if not self.data_ws:
            return False

        try:
            # Generate valid audio
            audio_data = self.generate_test_audio(duration_ms=200)
            checksum = self.calculate_checksum(audio_data)
            length = len(np.frombuffer(audio_data, dtype=np.int16))

            metadata = {
                'sampleRate': self.sample_rate,
                'dataLength': length,
                'checksum': checksum,
                'timestamp': int(time.time() * 1000),
                'server_sent_to_stt': True
            }

            # Encode and send
            metadata_json = json.dumps(metadata)
            metadata_bytes = metadata_json.encode('utf-8')
            metadata_length = struct.pack('<I', len(metadata_bytes))

            message = metadata_length + metadata_bytes + audio_data
            await self.data_ws.send(message)

            self.chunks_sent += 1
            print(f"[SEND] Sent valid chunk {self.chunks_sent}")
            return True

        except websockets.exceptions.ConnectionClosed:
            print("[INFO] Connection closed while sending")
            self.connection_closed = True
            return False
        except Exception as e:
            print(f"[ERROR] Error sending valid chunk: {e}")
            return False

    async def test_rejection_threshold(self, threshold):
        """Test rejection with specific threshold"""
        print(f"\n--- Testing Rejection with Threshold {threshold} ---")
        print("Sending corrupted chunks until server rejects us...")

        if not await self.connect():
            return False

        # Start message handler
        data_task = asyncio.create_task(self.handle_data_messages())

        try:
            # Send corrupted chunks until rejected or threshold exceeded
            for i in range(threshold + 5):  # Send more than threshold
                if self.connection_closed or self.rejection_received:
                    break

                success = await self.send_corrupted_chunk("wrong_checksum")
                if not success:
                    break

                await asyncio.sleep(0.5)  # Wait between chunks

            # Wait a bit for final server response
            if not self.connection_closed:
                await asyncio.sleep(2)

            # Try to send one more chunk to confirm disconnection
            if not self.connection_closed:
                print("\n[TEST] Attempting to send after threshold...")
                await self.send_corrupted_chunk("wrong_checksum")
                await asyncio.sleep(1)

        except Exception as e:
            print(f"[ERROR] Test failed: {e}")

        finally:
            data_task.cancel()
            if self.control_ws:
                await self.control_ws.close()
            if self.data_ws:
                await self.data_ws.close()

        # Report results
        print(f"\n[RESULTS]")
        print(f"  Chunks sent: {self.chunks_sent}")
        print(f"  Rejection received: {self.rejection_received}")
        print(f"  Connection closed: {self.connection_closed}")

        if threshold == 0:
            expected = "immediate rejection"
            success = self.chunks_sent <= 1 and (self.rejection_received or self.connection_closed)
        else:
            expected = f"rejection after {threshold} failures"
            success = self.chunks_sent <= threshold + 1 and (self.rejection_received or self.connection_closed)

        print(f"  Expected: {expected}")
        print(f"  Test result: {'PASS' if success else 'FAIL'}")

        return success


async def main():
    """Test the rejection system with different server configurations"""
    print("=" * 60)
    print("🛡️  Testing Server Rejection System")
    print("=" * 60)

    print("\n⚠️  INSTRUCTIONS:")
    print("1. Start server with: stt-server --verify-data-integrity --reject-corrupted-data --corruption-threshold 0")
    print("2. Run this test")
    print("3. Restart server with --corruption-threshold 2 and run again")
    print("\nPress Enter to continue...")
    input()

    # Test immediate rejection (threshold = 0)
    print("\n🔬 Test 1: Immediate Rejection (threshold=0)")
    client1 = RejectionTestClient()
    result1 = await client1.test_rejection_threshold(0)

    await asyncio.sleep(2)  # Wait between tests

    print(f"\n{'=' * 60}")
    print(f"📊 Final Results:")
    print(f"  Test 1 (immediate rejection): {'PASS' if result1 else 'FAIL'}")

    if result1:
        print(f"\n✅ Rejection system is working correctly!")
        print(f"💡 Try different thresholds with --corruption-threshold N")
    else:
        print(f"\n❌ Rejection system may have issues")
        print(f"💡 Check server logs and configuration")

    print(f"\n🔧 Server Configuration Examples:")
    print(f"  # Immediate rejection:")
    print(f"  stt-server --verify-data-integrity --reject-corrupted-data --corruption-threshold 0")
    print(f"  ")
    print(f"  # Allow 3 failures before rejection:")
    print(f"  stt-server --verify-data-integrity --reject-corrupted-data --corruption-threshold 3")
    print(f"  ")
    print(f"  # Verify but don't reject:")
    print(f"  stt-server --verify-data-integrity")


if __name__ == "__main__":
    asyncio.run(main())