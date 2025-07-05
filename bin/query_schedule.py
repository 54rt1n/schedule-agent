#!.venv/bin/python
from mcp.client.sse import sse_client
from mcp import ClientSession
import asyncio
import json
import argparse
from datetime import datetime

async def run_sse_client(protocol, host, port, start_time, end_time, title_contains):
    # Construct SSE URL from protocol, host and port
    sse_url = f"{protocol}://{host}:{port}/sse"
    print(f"Connecting to: {sse_url}")

    # Connect to the SSE endpoint
    async with sse_client(sse_url) as (read, write):
        print(f"Connected to: {sse_url}")
        async with ClientSession(read, write) as session:
            # Initialize the connection
            print("Initializing session...")
            try:
                init_result = await session.initialize()
                print(f"Initialization result: {init_result}")
            except Exception as e:
                print(f"Error initializing session: {e}")
                return
            print("Session initialized")

            # Prepare query parameters
            query_params = {}
            if start_time:
                query_params["start_time"] = start_time
            if end_time:
                query_params["end_time"] = end_time
            if title_contains:
                query_params["title_contains"] = title_contains

            print(f"Querying events with parameters: {query_params}")
            
            # Call the query_events_tool
            try:
                result = await session.call_tool("query_events", query_params)
                
                print("\n==== Query Results ====\n")
                
                # Parse the result
                if hasattr(result, 'content') and result.content:
                    # Extract the actual result from the content
                    content = result.content[0]  # Assuming first content item
                    if hasattr(content, 'text'):
                        # Parse JSON from text content
                        response_data = json.loads(content.text)
                        
                        if response_data.get("success"):
                            count = response_data.get("count", 0)
                            events = response_data.get("events", [])
                            
                            print(f"Found {count} events:")
                            print("-" * 50)
                            
                            if events:
                                for i, event in enumerate(events, 1):
                                    print(f"{i}. {event.get('title', 'No Title')}")
                                    print(f"   ID: {event.get('id')}")
                                    print(f"   Start: {event.get('start_time')}")
                                    print(f"   End: {event.get('end_time')}")
                                    if event.get('location'):
                                        print(f"   Location: {event.get('location')}")
                                    if event.get('notes'):
                                        print(f"   Notes: {event.get('notes')}")
                                    if event.get('rrule'):
                                        print(f"   Recurrence: {event.get('rrule')}")
                                    print()
                            else:
                                print("No events found matching the criteria.")
                        else:
                            error = response_data.get("error", "Unknown error")
                            print(f"❌ Query failed: {error}")
                    else:
                        print(f"Unexpected content format: {content}")
                else:
                    print(f"Unexpected result format: {result}")
                    
            except Exception as e:
                print(f"Error calling query_events_tool: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Query calendar events from MCP server")
    parser.add_argument("--protocol", default="http", choices=["http", "https"], help="Protocol to use (default: http)")
    parser.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    parser.add_argument("--port", type=int, default=8081, help="Server port (default: 8081)")
    parser.add_argument("--start-time", help="Start time for query (ISO 8601 or natural language like 'today', 'tomorrow 9am')")
    parser.add_argument("--end-time", help="End time for query (ISO 8601 or natural language)")
    parser.add_argument("--title-contains", help="Filter events by title (case-insensitive partial match)")
    
    args = parser.parse_args()
    
    asyncio.run(run_sse_client(
        args.protocol, 
        args.host, 
        args.port, 
        args.start_time, 
        args.end_time, 
        args.title_contains
    )) 