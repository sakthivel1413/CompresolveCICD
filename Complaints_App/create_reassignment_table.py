"""
Script to create the ReassignmentRequests DynamoDB table.
Run this script once to create the table in AWS DynamoDB.

Table Schema:
- RequestId (String) - Partition Key (Primary Key)
- ComplaintId (String) - The ticket being requested for reassignment
- TicketSubject (String) - Subject of the ticket
- TicketPriority (String) - Priority level
- RequestedBy (String) - Agent's email
- RequestedByName (String) - Agent's name
- AgentTeam (String) - Agent's team/group
- Reason (String) - Reason for reassignment
- AgentComments (String) - Additional comments from agent
- Status (String) - Pending, Approved, Rejected
- CreatedAt (String) - ISO timestamp when created
- UpdatedAt (String) - ISO timestamp when last updated
- SupervisorEmail (String) - Supervisor who processed the request
- SupervisorName (String) - Supervisor's name
- SupervisorComments (String) - Supervisor's comments
- ProcessedAt (String) - ISO timestamp when processed
"""

import boto3
import sys
import os

# Add parent directory to path to access settings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Complaints_Project.settings')

try:
    import django
    django.setup()
    from django.conf import settings
    
    AWS_ACCESS_KEY_ID = settings.AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = settings.AWS_SECRET_ACCESS_KEY
    AWS_REGION = settings.AWS_REGION_NAME
except:
    # Fallback if Django isn't available
    print("Django settings not available. Please provide AWS credentials manually.")
    print("Set environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION")
    sys.exit(1)


def create_table():
    """Create the ReassignmentRequests DynamoDB table."""
    
    dynamodb = boto3.resource(
        'dynamodb',
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )
    
    # Check if table already exists
    existing_tables = dynamodb.meta.client.list_tables()['TableNames']
    if 'ReassignmentRequests' in existing_tables:
        print("Table 'ReassignmentRequests' already exists!")
        return
    
    print("Creating 'ReassignmentRequests' table...")
    
    table = dynamodb.create_table(
        TableName='ReassignmentRequests',
        KeySchema=[
            {
                'AttributeName': 'RequestId',
                'KeyType': 'HASH'  # Partition key
            }
        ],
        AttributeDefinitions=[
            {
                'AttributeName': 'RequestId',
                'AttributeType': 'S'  # String
            }
        ],
        BillingMode='PAY_PER_REQUEST'  # On-demand pricing (no need to provision throughput)
    )
    
    # Wait for table to be created
    print("Waiting for table to be active...")
    table.meta.client.get_waiter('table_exists').wait(TableName='ReassignmentRequests')
    
    print("Table 'ReassignmentRequests' created successfully!")
    print(f"Table ARN: {table.table_arn}")
    print(f"Table Status: {table.table_status}")


if __name__ == '__main__':
    create_table()
