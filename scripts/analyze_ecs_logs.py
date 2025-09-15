#!/usr/bin/env python3
"""
ECS Log Analysis Script for Direct Model Service Verification.

This script helps analyze ECS CloudWatch logs to verify that the Direct Model
Service is working correctly in the ECS environment.

Usage:
    # Analyze logs from the last hour
    python scripts/analyze_ecs_logs.py --log-group /aws/ecs/morpheus-api-dev --hours 1

    # Analyze logs for specific time period
    python scripts/analyze_ecs_logs.py --log-group /aws/ecs/morpheus-api-dev --start "2025-01-15 10:00" --end "2025-01-15 11:00"

    # Focus on model-related logs only
    python scripts/analyze_ecs_logs.py --log-group /aws/ecs/morpheus-api-dev --hours 2 --filter-models
"""

import argparse
import json
import re
import sys
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


class ECSLogAnalyzer:
    """Analyzer for ECS CloudWatch logs to verify model service health."""
    
    def __init__(self, log_group: str, region: str = "us-east-2"):
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 is required. Install with: pip install boto3")
        
        self.log_group = log_group
        self.region = region
        self.client = boto3.client('logs', region_name=region)
        
        # Model service log patterns
        self.patterns = {
            'model_service_init': r'DirectModelService initialized with \d+s cache duration',
            'model_fetch_start': r'Fetching models from https://active\.(dev\.)?mor\.org/active_models\.json',
            'model_fetch_success': r'Successfully refreshed \d+ models',
            'model_fetch_304': r'Models data unchanged \(304 Not Modified\)',
            'model_fetch_hash': r'Models data unchanged \(same hash\)',
            'model_cache_extend': r'Cache extended for \d+ seconds',
            'model_resolution': r'\[MODEL_DEBUG\] Found mapping: .+ -> 0x[a-fA-F0-9]+',
            'model_fallback': r'\[MODEL_DEBUG\] Using default model',
            'model_error': r'Error (fetching|resolving) model',
            'cache_stats': r'Cache updated: \d+ model mappings',
            'health_check': r'GET /health',
            'model_health_check': r'GET /health/models',
            'startup_models': r'Direct model service initialized with \d+ models'
        }
    
    def parse_timestamp(self, timestamp_str: str) -> datetime:
        """Parse timestamp from various formats."""
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d"
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(timestamp_str, fmt)
            except ValueError:
                continue
        
        raise ValueError(f"Unable to parse timestamp: {timestamp_str}")
    
    async def get_logs(self, start_time: datetime, end_time: datetime, filter_pattern: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve logs from CloudWatch."""
        try:
            # Convert to milliseconds since epoch
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)
            
            # Get log streams
            response = self.client.describe_log_streams(
                logGroupName=self.log_group,
                orderBy='LastEventTime',
                descending=True,
                limit=50  # Get recent streams
            )
            
            all_events = []
            
            for stream in response['logStreams']:
                stream_name = stream['logStreamName']
                
                try:
                    # Get events from this stream
                    kwargs = {
                        'logGroupName': self.log_group,
                        'logStreamName': stream_name,
                        'startTime': start_ms,
                        'endTime': end_ms
                    }
                    
                    if filter_pattern:
                        kwargs['filterPattern'] = filter_pattern
                    
                    events_response = self.client.get_log_events(**kwargs)
                    
                    for event in events_response['events']:
                        event['logStream'] = stream_name
                        all_events.append(event)
                        
                except ClientError as e:
                    if e.response['Error']['Code'] != 'ResourceNotFoundException':
                        print(f"Warning: Error accessing log stream {stream_name}: {e}")
            
            # Sort by timestamp
            all_events.sort(key=lambda x: x['timestamp'])
            return all_events
            
        except ClientError as e:
            print(f"Error accessing CloudWatch logs: {e}")
            return []
        except NoCredentialsError:
            print("Error: AWS credentials not found. Please configure AWS credentials.")
            return []
    
    def analyze_model_service_health(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze model service health from log events."""
        analysis = {
            'total_events': len(events),
            'time_range': {},
            'model_service_events': defaultdict(int),
            'errors': [],
            'warnings': [],
            'model_fetches': [],
            'cache_operations': [],
            'model_resolutions': [],
            'health_checks': 0,
            'startup_info': {}
        }
        
        if events:
            analysis['time_range'] = {
                'start': datetime.fromtimestamp(events[0]['timestamp'] / 1000).isoformat(),
                'end': datetime.fromtimestamp(events[-1]['timestamp'] / 1000).isoformat()
            }
        
        for event in events:
            message = event['message']
            timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
            
            # Check for model service patterns
            for pattern_name, pattern in self.patterns.items():
                if re.search(pattern, message):
                    analysis['model_service_events'][pattern_name] += 1
                    
                    # Extract specific information
                    if pattern_name == 'model_fetch_success':
                        match = re.search(r'Successfully refreshed (\d+) models', message)
                        if match:
                            analysis['model_fetches'].append({
                                'timestamp': timestamp.isoformat(),
                                'model_count': int(match.group(1)),
                                'type': 'success'
                            })
                    
                    elif pattern_name in ['model_fetch_304', 'model_fetch_hash']:
                        analysis['cache_operations'].append({
                            'timestamp': timestamp.isoformat(),
                            'type': 'cache_hit',
                            'reason': '304 Not Modified' if pattern_name == 'model_fetch_304' else 'same hash'
                        })
                    
                    elif pattern_name == 'model_resolution':
                        match = re.search(r'Found mapping: (.+) -> (0x[a-fA-F0-9]+)', message)
                        if match:
                            analysis['model_resolutions'].append({
                                'timestamp': timestamp.isoformat(),
                                'model_name': match.group(1),
                                'blockchain_id': match.group(2)
                            })
                    
                    elif pattern_name == 'startup_models':
                        match = re.search(r'initialized with (\d+) models', message)
                        if match:
                            analysis['startup_info'] = {
                                'timestamp': timestamp.isoformat(),
                                'initial_model_count': int(match.group(1))
                            }
                    
                    elif pattern_name in ['health_check', 'model_health_check']:
                        analysis['health_checks'] += 1
            
            # Check for errors and warnings
            if re.search(r'ERROR|Error|error.*model', message, re.IGNORECASE):
                analysis['errors'].append({
                    'timestamp': timestamp.isoformat(),
                    'message': message.strip(),
                    'log_stream': event.get('logStream', 'unknown')
                })
            
            elif re.search(r'WARNING|Warning|warn.*model', message, re.IGNORECASE):
                analysis['warnings'].append({
                    'timestamp': timestamp.isoformat(),
                    'message': message.strip(),
                    'log_stream': event.get('logStream', 'unknown')
                })
        
        return analysis
    
    def generate_health_report(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a health report based on log analysis."""
        report = {
            'timestamp': datetime.now().isoformat(),
            'log_group': self.log_group,
            'analysis_period': analysis['time_range'],
            'overall_health': 'unknown',
            'summary': {},
            'recommendations': [],
            'detailed_analysis': analysis
        }
        
        # Calculate health score
        health_indicators = {
            'model_fetches': len(analysis['model_fetches']),
            'successful_resolutions': len(analysis['model_resolutions']),
            'cache_hits': len([op for op in analysis['cache_operations'] if op['type'] == 'cache_hit']),
            'errors': len(analysis['errors']),
            'health_checks': analysis['health_checks']
        }
        
        # Health assessment
        has_errors = len(analysis['errors']) > 0
        has_model_activity = health_indicators['model_fetches'] > 0 or health_indicators['successful_resolutions'] > 0
        has_startup_info = bool(analysis['startup_info'])
        
        if has_errors:
            report['overall_health'] = 'unhealthy'
            report['recommendations'].append("Investigate error messages in the logs")
        elif not has_model_activity and not has_startup_info:
            report['overall_health'] = 'warning'
            report['recommendations'].append("No model service activity detected - check if service is running")
        elif has_model_activity or has_startup_info:
            report['overall_health'] = 'healthy'
        
        # Summary statistics
        report['summary'] = {
            'total_log_events': analysis['total_events'],
            'model_service_events': sum(analysis['model_service_events'].values()),
            'model_fetches': health_indicators['model_fetches'],
            'model_resolutions': health_indicators['successful_resolutions'],
            'cache_operations': len(analysis['cache_operations']),
            'errors': health_indicators['errors'],
            'warnings': len(analysis['warnings']),
            'health_checks': health_indicators['health_checks']
        }
        
        # Add recommendations based on analysis
        if health_indicators['cache_hits'] > health_indicators['model_fetches']:
            report['recommendations'].append("Good cache performance - most requests using cached data")
        
        if health_indicators['model_fetches'] == 0 and has_model_activity:
            report['recommendations'].append("Models being resolved from cache - service working efficiently")
        
        if len(analysis['warnings']) > 0:
            report['recommendations'].append(f"Review {len(analysis['warnings'])} warning messages")
        
        return report
    
    def print_report(self, report: Dict[str, Any], verbose: bool = False):
        """Print a formatted health report."""
        print(f"\n{'='*60}")
        print("ECS MODEL SERVICE HEALTH REPORT")
        print(f"{'='*60}")
        
        print(f"Log Group: {report['log_group']}")
        print(f"Analysis Time: {report['timestamp']}")
        print(f"Period: {report['analysis_period'].get('start', 'unknown')} to {report['analysis_period'].get('end', 'unknown')}")
        print(f"Overall Health: {report['overall_health'].upper()}")
        
        print(f"\n{'SUMMARY':-^40}")
        summary = report['summary']
        print(f"Total Log Events: {summary['total_log_events']}")
        print(f"Model Service Events: {summary['model_service_events']}")
        print(f"Model Fetches: {summary['model_fetches']}")
        print(f"Model Resolutions: {summary['model_resolutions']}")
        print(f"Cache Operations: {summary['cache_operations']}")
        print(f"Health Checks: {summary['health_checks']}")
        print(f"Errors: {summary['errors']}")
        print(f"Warnings: {summary['warnings']}")
        
        # Startup info
        startup_info = report['detailed_analysis'].get('startup_info', {})
        if startup_info:
            print(f"\n{'STARTUP INFO':-^40}")
            print(f"Service Started: {startup_info['timestamp']}")
            print(f"Initial Models Loaded: {startup_info['initial_model_count']}")
        
        # Recent model fetches
        model_fetches = report['detailed_analysis']['model_fetches']
        if model_fetches:
            print(f"\n{'RECENT MODEL FETCHES':-^40}")
            for fetch in model_fetches[-5:]:  # Show last 5
                print(f"  {fetch['timestamp']}: {fetch['model_count']} models ({fetch['type']})")
        
        # Recent resolutions
        resolutions = report['detailed_analysis']['model_resolutions']
        if resolutions and verbose:
            print(f"\n{'RECENT MODEL RESOLUTIONS':-^40}")
            for resolution in resolutions[-10:]:  # Show last 10
                print(f"  {resolution['timestamp']}: {resolution['model_name']} -> {resolution['blockchain_id']}")
        
        # Errors
        errors = report['detailed_analysis']['errors']
        if errors:
            print(f"\n{'ERRORS':-^40}")
            for error in errors[-5:]:  # Show last 5 errors
                print(f"  {error['timestamp']}: {error['message']}")
        
        # Recommendations
        if report['recommendations']:
            print(f"\n{'RECOMMENDATIONS':-^40}")
            for i, rec in enumerate(report['recommendations'], 1):
                print(f"  {i}. {rec}")
        
        print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Analyze ECS logs for Direct Model Service health")
    parser.add_argument("--log-group", required=True, help="CloudWatch log group name")
    parser.add_argument("--region", default="us-east-2", help="AWS region")
    parser.add_argument("--hours", type=int, help="Hours back from now to analyze")
    parser.add_argument("--start", help="Start time (YYYY-MM-DD HH:MM)")
    parser.add_argument("--end", help="End time (YYYY-MM-DD HH:MM)")
    parser.add_argument("--filter-models", action="store_true", help="Filter for model-related logs only")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--output", "-o", help="Output file for JSON report")
    
    args = parser.parse_args()
    
    if not BOTO3_AVAILABLE:
        print("Error: boto3 is required. Install with: pip install boto3")
        sys.exit(1)
    
    # Determine time range
    if args.hours:
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=args.hours)
    elif args.start and args.end:
        analyzer = ECSLogAnalyzer(args.log_group, args.region)
        start_time = analyzer.parse_timestamp(args.start)
        end_time = analyzer.parse_timestamp(args.end)
    else:
        # Default to last hour
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)
    
    print(f"Analyzing logs from {start_time} to {end_time}")
    
    # Create analyzer and get logs
    analyzer = ECSLogAnalyzer(args.log_group, args.region)
    
    # Set filter pattern for model-related logs
    filter_pattern = None
    if args.filter_models:
        filter_pattern = "model"
    
    # This is a synchronous call, but we'll simulate async for compatibility
    import asyncio
    
    async def run_analysis():
        events = await analyzer.get_logs(start_time, end_time, filter_pattern)
        
        if not events:
            print("No log events found for the specified time range.")
            return
        
        print(f"Retrieved {len(events)} log events")
        
        # Analyze logs
        analysis = analyzer.analyze_model_service_health(events)
        report = analyzer.generate_health_report(analysis)
        
        # Output report
        analyzer.print_report(report, args.verbose)
        
        # Save to file if requested
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"Detailed report saved to {args.output}")
        
        # Exit with appropriate code
        exit_code = 0 if report['overall_health'] == 'healthy' else 1
        return exit_code
    
    # For now, we'll make it synchronous since CloudWatch logs API is sync
    try:
        # Get log streams
        response = analyzer.client.describe_log_streams(
            logGroupName=args.log_group,
            orderBy='LastEventTime',
            descending=True,
            limit=50
        )
        
        all_events = []
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)
        
        for stream in response['logStreams']:
            stream_name = stream['logStreamName']
            
            try:
                kwargs = {
                    'logGroupName': args.log_group,
                    'logStreamName': stream_name,
                    'startTime': start_ms,
                    'endTime': end_ms
                }
                
                if filter_pattern:
                    kwargs['filterPattern'] = filter_pattern
                
                events_response = analyzer.client.get_log_events(**kwargs)
                
                for event in events_response['events']:
                    event['logStream'] = stream_name
                    all_events.append(event)
                    
            except ClientError as e:
                if e.response['Error']['Code'] != 'ResourceNotFoundException':
                    print(f"Warning: Error accessing log stream {stream_name}: {e}")
        
        if not all_events:
            print("No log events found for the specified time range.")
            sys.exit(0)
        
        print(f"Retrieved {len(all_events)} log events")
        
        # Sort by timestamp
        all_events.sort(key=lambda x: x['timestamp'])
        
        # Analyze logs
        analysis = analyzer.analyze_model_service_health(all_events)
        report = analyzer.generate_health_report(analysis)
        
        # Output report
        analyzer.print_report(report, args.verbose)
        
        # Save to file if requested
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"Detailed report saved to {args.output}")
        
        # Exit with appropriate code
        exit_code = 0 if report['overall_health'] == 'healthy' else 1
        sys.exit(exit_code)
        
    except ClientError as e:
        print(f"Error accessing CloudWatch logs: {e}")
        sys.exit(1)
    except NoCredentialsError:
        print("Error: AWS credentials not found. Please configure AWS credentials.")
        sys.exit(1)


if __name__ == "__main__":
    main()
