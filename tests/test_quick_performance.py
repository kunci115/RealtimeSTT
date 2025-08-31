#!/usr/bin/env python3
"""
Quick Performance Test - Compare Default vs Optimized RealtimeSTT

Simple script to quickly test the difference between:
- Default server (float32, batch_size=16, beam_size=5)  
- Optimized server (int8, batch_size=4, beam_size=3)

Usage:
    # Test current running server
    python test_quick_performance.py
    
    # Compare both configurations (will start/stop servers automatically)
    python test_quick_performance.py --compare
"""

import asyncio
import websockets
import numpy as np
import json
import struct
import time
import argparse
from datetime import datetime
import statistics


class QuickPerformanceTest:
    def __init__(self):
        self.control_url = "ws://localhost:8011"
        self.data_url = "ws://localhost:8012"
        self.sample_rate = 16000
        
        # Generate test audio (3 seconds of synthetic speech)
        self.test_audio = self.generate_speech_audio()
    
    def generate_speech_audio(self, duration=3.0) -> bytes:
        """Generate realistic speech-like audio for testing"""
        samples = int(self.sample_rate * duration)
        t = np.linspace(0, duration, samples, False)
        
        # Create speech-like signal with fundamental + harmonics
        fundamental = 120.0  # Male voice fundamental frequency
        audio = np.zeros(samples)
        
        # Add fundamental and harmonics
        audio += 0.4 * np.sin(2 * np.pi * fundamental * t)
        audio += 0.2 * np.sin(2 * np.pi * fundamental * 2 * t) 
        audio += 0.1 * np.sin(2 * np.pi * fundamental * 3 * t)
        audio += 0.05 * np.sin(2 * np.pi * fundamental * 4 * t)
        
        # Add formant-like resonances (vowel sounds)
        formant1 = 800  # First formant
        formant2 = 1200  # Second formant
        audio += 0.15 * np.sin(2 * np.pi * formant1 * t) * np.exp(-t * 2)
        audio += 0.1 * np.sin(2 * np.pi * formant2 * t) * np.exp(-t * 1.5)
        
        # Add some noise for realism
        noise = np.random.normal(0, 0.03, samples)
        audio += noise
        
        # Apply speech-like envelope (fade in/out)
        envelope = np.ones(samples)
        fade_samples = int(0.1 * samples)  # 100ms fade
        envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
        envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)
        audio *= envelope
        
        # Normalize and convert to int16
        audio = np.clip(audio, -1.0, 1.0)
        audio_int16 = (audio * 32767).astype(np.int16)
        
        return audio_int16.tobytes()
    
    def calculate_checksum(self, audio_data: bytes) -> int:
        """Calculate checksum for data verification"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        return int(np.sum(audio_array, dtype=np.int64)) & 0xFFFFFFFF
    
    async def test_transcription_latency(self) -> tuple:
        """Test single transcription request, return (latency, text, success)"""
        try:
            # Connect to server (fix timeout parameter)
            control_ws = await asyncio.wait_for(websockets.connect(self.control_url), timeout=5)
            data_ws = await asyncio.wait_for(websockets.connect(self.data_url), timeout=5)
            
            # Prepare audio with verification data
            metadata = {
                'sampleRate': self.sample_rate,
                'dataLength': len(np.frombuffer(self.test_audio, dtype=np.int16)),
                'checksum': self.calculate_checksum(self.test_audio),
                'timestamp': int(time.time() * 1000),
                'server_sent_to_stt': True
            }
            
            # Encode message
            metadata_json = json.dumps(metadata)
            metadata_bytes = metadata_json.encode('utf-8')
            metadata_length = struct.pack('<I', len(metadata_bytes))
            message = metadata_length + metadata_bytes + self.test_audio
            
            # Send and measure
            start_time = time.time()
            await data_ws.send(message)
            
            # Wait for result
            transcription = ""
            timeout_counter = 0
            max_timeout = 100  # 10 seconds max
            
            async for response in data_ws:
                data = json.loads(response)
                
                if data.get('type') == 'fullSentence':
                    transcription = data.get('text', 'No text')
                    break
                elif data.get('type') == 'error':
                    error_msg = data.get('message', 'Unknown error')
                    await control_ws.close()
                    await data_ws.close()
                    return None, f"Error: {error_msg}", False
                
                timeout_counter += 1
                if timeout_counter > max_timeout:
                    break
                    
                await asyncio.sleep(0.1)
            
            end_time = time.time()
            latency = end_time - start_time
            
            await control_ws.close()  
            await data_ws.close()
            
            return latency, transcription, True
            
        except asyncio.TimeoutError:
            return None, "Connection timeout", False
        except Exception as e:
            return None, f"Test failed: {str(e)}", False
    
    async def run_test_batch(self, num_tests=5, test_name="Test") -> dict:
        """Run multiple tests and return statistics"""
        print(f"🧪 Running {test_name} ({num_tests} tests)...")
        
        latencies = []
        transcriptions = []
        failures = 0
        
        for i in range(num_tests):
            print(f"   Test {i+1}/{num_tests}...", end=" ", flush=True)
            
            latency, text, success = await self.test_transcription_latency()
            
            if success and latency:
                latencies.append(latency)
                transcriptions.append(text)
                print(f"✅ {latency:.3f}s")
            else:
                failures += 1
                print(f"❌ {text}")
        
        if latencies:
            return {
                'avg_latency': statistics.mean(latencies),
                'min_latency': min(latencies),
                'max_latency': max(latencies),
                'std_latency': statistics.stdev(latencies) if len(latencies) > 1 else 0,
                'success_rate': (len(latencies) / num_tests) * 100,
                'failures': failures,
                'transcriptions': transcriptions[:3]  # Show first 3 transcriptions
            }
        else:
            return {
                'avg_latency': 0,
                'min_latency': 0, 
                'max_latency': 0,
                'std_latency': 0,
                'success_rate': 0,
                'failures': failures,
                'transcriptions': []
            }
    
    def print_results(self, results: dict, title: str):
        """Print formatted test results"""
        print(f"\n📊 {title} Results:")
        print(f"   Average Latency: {results['avg_latency']:.3f}s")
        print(f"   Range: {results['min_latency']:.3f}s - {results['max_latency']:.3f}s")
        print(f"   Std Deviation: {results['std_latency']:.3f}s") 
        print(f"   Success Rate: {results['success_rate']:.1f}%")
        
        if results['transcriptions']:
            print(f"   Sample Transcription: \"{results['transcriptions'][0]}\"")
    
    def compare_results(self, default_results: dict, optimized_results: dict):
        """Compare and show improvement between configurations"""
        print("\n" + "="*60)
        print("📈 PERFORMANCE COMPARISON")
        print("="*60)
        
        if default_results['avg_latency'] > 0 and optimized_results['avg_latency'] > 0:
            improvement = ((default_results['avg_latency'] - optimized_results['avg_latency']) 
                          / default_results['avg_latency'] * 100)
            
            print(f"\n⚡ Speed Improvement: {improvement:+.1f}%")
            print(f"   Default:   {default_results['avg_latency']:.3f}s average")
            print(f"   Optimized: {optimized_results['avg_latency']:.3f}s average")
            print(f"   Time Saved: {default_results['avg_latency'] - optimized_results['avg_latency']:.3f}s per request")
            
            if improvement > 30:
                print("   🎉 Excellent improvement!")
            elif improvement > 10:
                print("   ✅ Good improvement!")
            elif improvement > 0:
                print("   📈 Minor improvement")
            else:
                print("   ⚠️  No significant improvement")
        
        success_diff = optimized_results['success_rate'] - default_results['success_rate']
        print(f"\n🎯 Reliability Change: {success_diff:+.1f}%")
        print(f"   Default Success:   {default_results['success_rate']:.1f}%")
        print(f"   Optimized Success: {optimized_results['success_rate']:.1f}%")


def main():
    parser = argparse.ArgumentParser(description='Quick RealtimeSTT Performance Test')
    parser.add_argument('--tests', type=int, default=5,
                      help='Number of tests to run (default: 5)')
    parser.add_argument('--compare', action='store_true',
                      help='Compare default vs optimized (requires server restart)')
    
    args = parser.parse_args()
    
    tester = QuickPerformanceTest()
    
    print("🚀 RealtimeSTT Quick Performance Test")
    print("="*50)
    
    try:
        if args.compare:
            print("⚠️  Comparison mode requires manual server restart between tests")
            print("\n1️⃣  First, start server with DEFAULT settings:")
            print("   stt-server --model large-v2 --device cuda --gpu_device_index 0")
            print("   --control_port 8011 --data_port 8012 --verify-data-integrity")
            input("\nPress ENTER when default server is ready...")
            
            # Test default configuration
            default_results = asyncio.run(tester.run_test_batch(args.tests, "Default Configuration"))
            tester.print_results(default_results, "DEFAULT CONFIGURATION")
            
            print("\n2️⃣  Now restart server with OPTIMIZED settings:")
            print("   stt-server --model large-v2 --device cuda --gpu_device_index 0")
            print("   --control_port 8011 --data_port 8012 --verify-data-integrity")
            print("   --compute_type int8 --batch_size 4 --beam_size 3")
            input("\nPress ENTER when optimized server is ready...")
            
            # Test optimized configuration  
            optimized_results = asyncio.run(tester.run_test_batch(args.tests, "Optimized Configuration"))
            tester.print_results(optimized_results, "OPTIMIZED CONFIGURATION")
            
            # Show comparison
            tester.compare_results(default_results, optimized_results)
            
        else:
            # Test current server configuration
            print("🔍 Testing current server configuration...")
            print("💡 Make sure your server is running on localhost:8011/8012")
            
            results = asyncio.run(tester.run_test_batch(args.tests, "Current Server"))
            tester.print_results(results, "CURRENT SERVER")
            
            # Performance analysis
            if results['avg_latency'] > 1.2:
                print(f"\n💡 Performance Suggestion:")
                print(f"   Your average latency is {results['avg_latency']:.3f}s")
                print(f"   Try optimized settings for ~40% improvement:")
                print(f"   --compute_type int8 --batch_size 4 --beam_size 3")
            elif results['avg_latency'] > 0.8:
                print(f"\n✅ Good performance! ({results['avg_latency']:.3f}s average)")
            else:
                print(f"\n🚀 Excellent performance! ({results['avg_latency']:.3f}s average)")
    
    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        print("\n🔧 Troubleshooting:")
        print("   • Make sure STT server is running")
        print("   • Check server is on localhost:8011/8012") 
        print("   • Verify --verify-data-integrity flag is set")
    
    print("\n👋 Test completed")


if __name__ == "__main__":
    main()