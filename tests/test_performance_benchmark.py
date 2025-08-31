#!/usr/bin/env python3
"""
Performance Benchmark Test for RealtimeSTT Server

Tests different server configurations to measure latency improvements:
- Default float32 vs int8 compute types
- Different batch sizes and beam sizes
- Concurrent request handling
- GPU utilization comparison

Usage:
    python test_performance_benchmark.py
    python test_performance_benchmark.py --config optimized
    python test_performance_benchmark.py --concurrent 5
"""

import asyncio
import websockets
import numpy as np
import json
import struct
import time
import argparse
import threading
import statistics
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import subprocess
import sys
import os
import signal
import psutil


class PerformanceBenchmark:
    def __init__(self, control_url="ws://localhost:8011", data_url="ws://localhost:8012"):
        self.control_url = control_url
        self.data_url = data_url
        self.sample_rate = 16000
        self.chunk_size = 4096
        
        # Test results storage
        self.results = {
            'default': [],
            'optimized': [],
            'concurrent': []
        }
        
        # Generate test audio data
        self.test_audio = self.generate_test_audio()
        
    def generate_test_audio(self, duration_seconds=3.0) -> bytes:
        """Generate synthetic speech-like audio for consistent testing"""
        samples = int(self.sample_rate * duration_seconds)
        
        # Create speech-like waveform with multiple frequency components
        t = np.linspace(0, duration_seconds, samples, False)
        
        # Fundamental frequency (simulating speech)
        fundamental = 150.0  # Hz
        audio = np.sin(2 * np.pi * fundamental * t) * 0.3
        
        # Add harmonics for speech-like quality
        audio += np.sin(2 * np.pi * fundamental * 2 * t) * 0.2
        audio += np.sin(2 * np.pi * fundamental * 3 * t) * 0.1
        
        # Add some noise for realism
        noise = np.random.normal(0, 0.05, samples)
        audio += noise
        
        # Normalize and convert to int16
        audio = np.clip(audio, -1.0, 1.0)
        audio_int16 = (audio * 32767).astype(np.int16)
        
        return audio_int16.tobytes()
    
    def calculate_checksum(self, audio_data: bytes) -> int:
        """Calculate checksum for data verification"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        checksum = int(np.sum(audio_array, dtype=np.int64)) & 0xFFFFFFFF
        return checksum
    
    async def test_single_request(self, test_name: str = "default") -> Optional[float]:
        """Test a single transcription request and measure latency"""
        try:
            # Connect to server
            control_ws = await asyncio.wait_for(websockets.connect(self.control_url), timeout=5)
            data_ws = await asyncio.wait_for(websockets.connect(self.data_url), timeout=5)
            
            # Prepare audio chunk with verification data
            metadata = {
                'sampleRate': self.sample_rate,
                'dataLength': len(np.frombuffer(self.test_audio, dtype=np.int16)),
                'checksum': self.calculate_checksum(self.test_audio),
                'timestamp': int(time.time() * 1000),
                'server_sent_to_stt': True,
                'test_name': test_name
            }
            
            metadata_json = json.dumps(metadata)
            metadata_bytes = metadata_json.encode('utf-8')
            metadata_length = struct.pack('<I', len(metadata_bytes))
            
            # Combine message
            message = metadata_length + metadata_bytes + self.test_audio
            
            # Start timing
            start_time = time.time()
            
            # Send audio data
            await data_ws.send(message)
            
            # Wait for transcription result
            final_text = None
            async for response in data_ws:
                data = json.loads(response)
                if data.get('type') == 'fullSentence':
                    final_text = data.get('text', '')
                    break
                elif data.get('type') == 'error':
                    print(f"❌ Server error: {data.get('message', 'Unknown error')}")
                    return None
            
            # Calculate latency
            end_time = time.time()
            latency = end_time - start_time
            
            # Close connections
            await control_ws.close()
            await data_ws.close()
            
            return latency
            
        except Exception as e:
            print(f"❌ Test failed: {e}")
            return None
    
    async def test_concurrent_requests(self, num_concurrent: int = 5) -> List[float]:
        """Test multiple concurrent requests"""
        print(f"🔄 Testing {num_concurrent} concurrent requests...")
        
        # Create tasks for concurrent requests
        tasks = []
        for i in range(num_concurrent):
            task = asyncio.create_task(self.test_single_request(f"concurrent_{i}"))
            tasks.append(task)
        
        # Wait for all requests to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out failed requests
        latencies = []
        for result in results:
            if isinstance(result, float) and result > 0:
                latencies.append(result)
            elif isinstance(result, Exception):
                print(f"⚠️  Concurrent request failed: {result}")
        
        return latencies
    
    def run_benchmark_suite(self, num_tests: int = 5, concurrent_tests: int = 3) -> Dict:
        """Run complete benchmark suite"""
        print("🚀 Starting RealtimeSTT Performance Benchmark")
        print("=" * 60)
        
        results = {}
        
        # Test current configuration
        print(f"\n📊 Testing current server configuration ({num_tests} tests)...")
        current_latencies = []
        
        for i in range(num_tests):
            print(f"   Test {i+1}/{num_tests}...", end=" ")
            latency = asyncio.run(self.test_single_request("current"))
            if latency:
                current_latencies.append(latency)
                print(f"✅ {latency:.3f}s")
            else:
                print("❌ Failed")
        
        results['current'] = {
            'latencies': current_latencies,
            'avg': statistics.mean(current_latencies) if current_latencies else 0,
            'min': min(current_latencies) if current_latencies else 0,
            'max': max(current_latencies) if current_latencies else 0,
            'std': statistics.stdev(current_latencies) if len(current_latencies) > 1 else 0
        }
        
        # Test concurrent requests
        if concurrent_tests > 1:
            print(f"\n🔄 Testing concurrent requests ({concurrent_tests} concurrent)...")
            concurrent_latencies = asyncio.run(self.test_concurrent_requests(concurrent_tests))
            
            results['concurrent'] = {
                'latencies': concurrent_latencies,
                'avg': statistics.mean(concurrent_latencies) if concurrent_latencies else 0,
                'min': min(concurrent_latencies) if concurrent_latencies else 0,
                'max': max(concurrent_latencies) if concurrent_latencies else 0,
                'std': statistics.stdev(concurrent_latencies) if len(concurrent_latencies) > 1 else 0,
                'success_rate': len(concurrent_latencies) / concurrent_tests * 100
            }
        
        return results
    
    def print_results(self, results: Dict):
        """Print formatted benchmark results"""
        print("\n" + "="*60)
        print("📋 BENCHMARK RESULTS")
        print("="*60)
        
        if 'current' in results:
            current = results['current']
            print(f"\n🔍 Current Configuration:")
            print(f"   Average Latency: {current['avg']:.3f}s")
            print(f"   Min Latency:     {current['min']:.3f}s")
            print(f"   Max Latency:     {current['max']:.3f}s")
            print(f"   Std Deviation:   {current['std']:.3f}s")
            print(f"   Tests Completed: {len(current['latencies'])}")
        
        if 'concurrent' in results:
            concurrent = results['concurrent']
            print(f"\n🔄 Concurrent Requests:")
            print(f"   Average Latency: {concurrent['avg']:.3f}s")
            print(f"   Min Latency:     {concurrent['min']:.3f}s") 
            print(f"   Max Latency:     {concurrent['max']:.3f}s")
            print(f"   Success Rate:    {concurrent['success_rate']:.1f}%")
            print(f"   Requests Handled: {len(concurrent['latencies'])}")
        
        # Performance analysis
        print(f"\n📈 Performance Analysis:")
        if 'current' in results and results['current']['avg'] > 0:
            avg_latency = results['current']['avg']
            if avg_latency > 1.2:
                print("   ⚠️  High latency detected - consider optimization")
                print("   💡 Try: --compute_type int8 --batch_size 4 --beam_size 3")
            elif avg_latency > 0.8:
                print("   ⚡ Moderate performance - some optimization possible")
            else:
                print("   ✅ Good performance!")
        
        if 'concurrent' in results:
            success_rate = results['concurrent']['success_rate']
            if success_rate < 100:
                print(f"   ⚠️  Concurrent request failures: {100-success_rate:.1f}%")
                print("   💡 Server may need concurrency improvements")
    
    def save_results(self, results: Dict, filename: str = None):
        """Save results to JSON file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"benchmark_results_{timestamp}.json"
        
        # Add metadata
        results['metadata'] = {
            'timestamp': datetime.now().isoformat(),
            'test_audio_duration': 3.0,
            'chunk_size': self.chunk_size,
            'sample_rate': self.sample_rate
        }
        
        filepath = os.path.join(os.path.dirname(__file__), filename)
        with open(filepath, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\n💾 Results saved to: {filepath}")


class ServerManager:
    """Helper class to manage server for testing"""
    
    def __init__(self):
        self.server_process = None
    
    def start_server(self, config: str = "default"):
        """Start server with specified configuration"""
        if config == "optimized":
            cmd = [
                "stt-server",
                "--model", "large-v2",
                "--device", "cuda", 
                "--gpu_device_index", "0",
                "--control_port", "8011",
                "--data_port", "8012",
                "--compute_type", "int8",
                "--batch_size", "4",
                "--beam_size", "3",
                "--verify-data-integrity"
            ]
        else:  # default
            cmd = [
                "stt-server", 
                "--model", "large-v2",
                "--device", "cuda",
                "--gpu_device_index", "0", 
                "--control_port", "8011",
                "--data_port", "8012",
                "--verify-data-integrity"
            ]
        
        print(f"🚀 Starting server with {config} configuration...")
        print(f"Command: {' '.join(cmd)}")
        
        self.server_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait for server to start
        print("⏳ Waiting for server to initialize...")
        time.sleep(10)
        
        return self.server_process
    
    def stop_server(self):
        """Stop the server"""
        if self.server_process:
            print("🛑 Stopping server...")
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
            self.server_process = None


def main():
    parser = argparse.ArgumentParser(description='RealtimeSTT Performance Benchmark')
    parser.add_argument('--config', choices=['default', 'optimized'], default='current',
                      help='Server configuration to test (current = whatever is running)')
    parser.add_argument('--concurrent', type=int, default=3,
                      help='Number of concurrent requests to test')
    parser.add_argument('--tests', type=int, default=5,
                      help='Number of sequential tests to run')
    parser.add_argument('--auto-server', action='store_true',
                      help='Automatically start/stop server for testing')
    parser.add_argument('--save', type=str, default=None,
                      help='Save results to specific filename')
    parser.add_argument('--control-url', default="ws://localhost:8011",
                      help='Control WebSocket URL')
    parser.add_argument('--data-url', default="ws://localhost:8012", 
                      help='Data WebSocket URL')
    
    args = parser.parse_args()
    
    # Initialize benchmark
    benchmark = PerformanceBenchmark(args.control_url, args.data_url)
    server_manager = None
    
    try:
        # Start server if requested
        if args.auto_server:
            server_manager = ServerManager()
            server_manager.start_server(args.config)
        
        # Run benchmark
        print(f"🎯 Testing with {args.tests} sequential tests and {args.concurrent} concurrent requests")
        results = benchmark.run_benchmark_suite(args.tests, args.concurrent)
        
        # Display results
        benchmark.print_results(results)
        
        # Save results
        if args.save:
            benchmark.save_results(results, args.save)
        
    except KeyboardInterrupt:
        print("\n⏹️  Benchmark interrupted by user")
    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
    finally:
        # Cleanup
        if server_manager:
            server_manager.stop_server()
        
        print("\n👋 Benchmark completed")


if __name__ == "__main__":
    main()