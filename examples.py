#!/usr/bin/env python3
"""
Examples demonstrating the GitHub Engineering Intelligence API
"""

import requests
from datetime import datetime, timedelta
import json


BASE_URL = "http://localhost:8000/api"

# Example repository
REPO_URL = "https://github.com/microsoft/vscode"

# Time range: last 90 days
end_time = datetime.utcnow()
start_time = end_time - timedelta(days=90)


def example_1_basic_analysis():
    """Example 1: Basic repository analysis"""
    print("\n" + "="*80)
    print("EXAMPLE 1: Basic Repository Analysis")
    print("="*80)
    
    payload = {
        "repo_url": REPO_URL,
        "start_time": start_time.isoformat() + "Z",
        "end_time": end_time.isoformat() + "Z"
    }
    
    print(f"\nRequest: POST {BASE_URL}/analyze")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    response = requests.post(f"{BASE_URL}/analyze", json=payload)
    
    if response.status_code == 200:
        data = response.json()
        metrics = data["metrics"]
        analysis = data["analysis"]
        
        print(f"\n✓ Analysis completed successfully!")
        print(f"\nMetrics Summary:")
        print(f"  - Total PRs Merged: {metrics['total_prs_merged']}")
        print(f"  - Total Issues Closed: {metrics['total_issues_closed']}")
        print(f"  - Average Cycle Time: {metrics['avg_cycle_time_hours']:.1f} hours")
        print(f"  - Average Review Latency: {metrics['avg_review_latency_hours']:.1f} hours")
        print(f"  - Unique Contributors: {metrics['unique_contributors']}")
        print(f"  - Quality Score: {metrics['quality_score']:.2f}")
        print(f"  - Velocity Trend: {metrics['velocity_trend']}")
        
        print(f"\nKey Findings:")
        for finding in analysis["key_findings"][:3]:
            print(f"  • {finding}")
        
        print(f"\nTop 3 Recommendations:")
        for rec in analysis["recommendations"][:3]:
            print(f"  • {rec}")
    else:
        print(f"✗ Error: {response.status_code}")
        print(response.json())


def example_2_get_metrics_only():
    """Example 2: Get metrics without LLM analysis"""
    print("\n" + "="*80)
    print("EXAMPLE 2: Get Metrics Only (No LLM Analysis)")
    print("="*80)
    
    params = {
        "repo_url": REPO_URL,
        "start_time": start_time.isoformat() + "Z",
        "end_time": end_time.isoformat() + "Z"
    }
    
    print(f"\nRequest: GET {BASE_URL}/metrics")
    print(f"Query Params: {json.dumps(params, indent=2)}")
    
    response = requests.get(f"{BASE_URL}/metrics", params=params)
    
    if response.status_code == 200:
        metrics = response.json()
        
        print(f"\n✓ Metrics retrieved successfully!")
        print(f"\nTop 3 Contributors:")
        for contributor in metrics["top_contributors"][:3]:
            print(f"  • {contributor['username']}")
            print(f"    - PRs Merged: {contributor['prs_merged']}")
            print(f"    - Reviews: {contributor['reviews_completed']}")
            print(f"    - Score: {contributor['contribution_score']:.2f}")
    else:
        print(f"✗ Error: {response.status_code}")
        print(response.json())


def example_3_chat_single_turn():
    """Example 3: Single turn chat interaction"""
    print("\n" + "="*80)
    print("EXAMPLE 3: Chat - Single Turn Interaction")
    print("="*80)
    
    payload = {
        "message": "What are the top contributors and their contribution scores?",
        "repo_url": REPO_URL,
        "start_time": start_time.isoformat() + "Z",
        "end_time": end_time.isoformat() + "Z",
        "conversation_history": []
    }
    
    print(f"\nRequest: POST {BASE_URL}/chat")
    print(f"Payload: {json.dumps({'message': payload['message'], 'repo_url': REPO_URL}, indent=2)}")
    
    response = requests.post(f"{BASE_URL}/chat", json=payload)
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✓ Chat response received!")
        print(f"\nAssistant Response:")
        print(f"{data['message']}")
        print(f"\nConversation Turn: {data['conversation_turn']}")
    else:
        print(f"✗ Error: {response.status_code}")
        print(response.json())


def example_4_chat_multi_turn():
    """Example 4: Multi-turn chat conversation"""
    print("\n" + "="*80)
    print("EXAMPLE 4: Chat - Multi-Turn Conversation")
    print("="*80)
    
    conversation_history = []
    
    # First turn
    message_1 = "Analyze the velocity trend for this repository"
    print(f"\n[Turn 1] User: {message_1}")
    
    response_1 = requests.post(f"{BASE_URL}/chat", json={
        "message": message_1,
        "repo_url": REPO_URL,
        "start_time": start_time.isoformat() + "Z",
        "end_time": end_time.isoformat() + "Z",
        "conversation_history": []
    })
    
    if response_1.status_code == 200:
        data_1 = response_1.json()
        print(f"[Turn 1] Assistant: {data_1['message'][:200]}...")
        
        # Add to history
        conversation_history.append({
            "role": "user",
            "content": message_1
        })
        conversation_history.append({
            "role": "assistant",
            "content": data_1['message']
        })
        
        # Second turn - follow up
        message_2 = "Is the review process a bottleneck? What can we improve?"
        print(f"\n[Turn 2] User: {message_2}")
        
        response_2 = requests.post(f"{BASE_URL}/chat", json={
            "message": message_2,
            "repo_url": REPO_URL,
            "start_time": start_time.isoformat() + "Z",
            "end_time": end_time.isoformat() + "Z",
            "conversation_history": conversation_history
        })
        
        if response_2.status_code == 200:
            data_2 = response_2.json()
            print(f"[Turn 2] Assistant: {data_2['message'][:200]}...")
            print(f"\n✓ Multi-turn conversation completed!")
            print(f"Total turns in conversation: {data_2['conversation_turn']}")
        else:
            print(f"✗ Error in turn 2: {response_2.status_code}")
    else:
        print(f"✗ Error in turn 1: {response_1.status_code}")


def example_5_time_range_analysis():
    """Example 5: Compare different time ranges"""
    print("\n" + "="*80)
    print("EXAMPLE 5: Time Range Analysis - Recent vs Previous Quarter")
    print("="*80)
    
    # Recent: Last 30 days
    recent_end = datetime.utcnow()
    recent_start = recent_end - timedelta(days=30)
    
    # Previous: 30-60 days ago
    prev_end = recent_start
    prev_start = prev_end - timedelta(days=30)
    
    print(f"\nComparing:")
    print(f"  Recent Period: {recent_start.date()} to {recent_end.date()}")
    print(f"  Previous Period: {prev_start.date()} to {prev_end.date()}")
    
    # Get recent metrics
    print(f"\n📊 Fetching recent period metrics...")
    recent_resp = requests.get(f"{BASE_URL}/metrics", params={
        "repo_url": REPO_URL,
        "start_time": recent_start.isoformat() + "Z",
        "end_time": recent_end.isoformat() + "Z"
    })
    
    # Get previous metrics
    print(f"📊 Fetching previous period metrics...")
    prev_resp = requests.get(f"{BASE_URL}/metrics", params={
        "repo_url": REPO_URL,
        "start_time": prev_start.isoformat() + "Z",
        "end_time": prev_end.isoformat() + "Z"
    })
    
    if recent_resp.status_code == 200 and prev_resp.status_code == 200:
        recent = recent_resp.json()
        prev = prev_resp.json()
        
        print(f"\n✓ Metrics comparison:")
        print(f"\n{'Metric':<30} {'Previous':<15} {'Recent':<15} {'Trend':<10}")
        print("-" * 70)
        
        cycle_prev = prev["avg_cycle_time_hours"]
        cycle_recent = recent["avg_cycle_time_hours"]
        cycle_change = ((cycle_recent - cycle_prev) / cycle_prev * 100) if cycle_prev else 0
        trend = "📈" if cycle_change > 0 else "📉" if cycle_change < 0 else "➡️"
        
        print(f"{'Avg Cycle Time (hours)':<30} {cycle_prev:<15.1f} {cycle_recent:<15.1f} {trend} {cycle_change:+.1f}%")
        
        print(f"{'PRs Merged':<30} {prev['total_prs_merged']:<15} {recent['total_prs_merged']:<15}")
        print(f"{'Issues Closed':<30} {prev['total_issues_closed']:<15} {recent['total_issues_closed']:<15}")
        print(f"{'Quality Score':<30} {prev['quality_score']:<15.2f} {recent['quality_score']:<15.2f}")
    else:
        print(f"✗ Error fetching metrics")


def example_6_health_check():
    """Example 6: API Health Check"""
    print("\n" + "="*80)
    print("EXAMPLE 6: API Health Check")
    print("="*80)
    
    print(f"\nRequest: GET {BASE_URL}/health")
    
    response = requests.get(f"{BASE_URL}/health")
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✓ API is healthy!")
        print(f"  Status: {data['status']}")
        print(f"  Service: {data['service']}")
    else:
        print(f"✗ API health check failed: {response.status_code}")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("GitHub Engineering Intelligence API - Usage Examples")
    print("="*80)
    
    try:
        # Run examples
        example_6_health_check()
        example_1_basic_analysis()
        example_2_get_metrics_only()
        example_3_chat_single_turn()
        example_4_chat_multi_turn()
        example_5_time_range_analysis()
        
        print("\n" + "="*80)
        print("✓ All examples completed successfully!")
        print("="*80)
        print("\nFor more information:")
        print("  - API Docs: http://localhost:8000/docs")
        print("  - README: See README.md")
        print("  - Architecture: See ARCHITECTURE.md")
        
    except requests.exceptions.ConnectionError:
        print("\n✗ Could not connect to API")
        print("   Make sure the API is running: uvicorn main:app --reload")
        print("   Or use: docker-compose up")
    except Exception as e:
        print(f"\n✗ Error: {e}")
