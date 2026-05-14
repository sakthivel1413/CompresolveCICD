
import boto3
import json
from datetime import datetime, timedelta
from botocore.exceptions import ClientError
from decimal import Decimal

# Initialize DynamoDB client
# Note: In a real Lambda environment, boto3 is available by default.
# For local dev, we assume credentials are set in environment or via boto3 setup in views.py
dynamodb = boto3.resource('dynamodb')
table_name = 'Transaction' 
table = dynamodb.Table(table_name)

def lambda_handler(event, context):
    """
    AWS Lambda handler for Transaction Resolution Use Case.
    Event expected structure:
    {
        "transaction_id": "TXN-12345",
        "customer_id": "CUST-001",
        "description": "User complaint description..."
    }
    """
    print(f"Received event: {json.dumps(event)}")
    
    # Handle API Gateway Proxy Integration (where payload is in 'body' string)
    if 'body' in event:
        try:
            body = json.loads(event['body'])
            transaction_id = body.get('transaction_id')
            customer_id = body.get('customer_id')
            description = body.get('description', '').lower()
        except Exception as e:
            print(f"Error parsing body: {e}")
            transaction_id = None
    else:
        # Direct invocation or non-proxy integration
        transaction_id = event.get('transaction_id')
        customer_id = event.get('customer_id')
        description = event.get('description', '').lower()
    
    if not transaction_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'status': 'Error', 'message': 'Missing Transaction ID'})
        }

    try:
        # 1. Retrieve Transaction
        response = table.get_item(Key={'TransactionID': transaction_id})
        transaction = response.get('Item')
        
        if not transaction:
            return {
                'statusCode': 404,
                'body': json.dumps({'status': 'Error', 'message': f"Transaction {transaction_id} not found."})
            }
            
        # Verify Customer
        # Note: In a real app, strict auth checks needed.
        if transaction.get('CustomerID') != customer_id:
             return {
                'statusCode': 403,
                'body': json.dumps({'status': 'Error', 'message': "Transaction does not belong to this customer."})
            }

        txn_status = transaction.get('TransactionStatus')
        # Convert Decimal to float for comparison if using DynamoDB Decimals
        amount = float(transaction.get('TransactionAmount', 0))
        txn_type = transaction.get('TransactionType')
        retry_count = int(transaction.get('RetryCount', 0))
        
        result = {'status': 'Pending', 'message': 'Under Review'}

        # --- Scenario 1: Extra Charge (OTT Subscription) ---
        if "subscription" in description or "extra" in description or "charge" in description:
            # Mock expected fee logic
            EXPECTED_FEE = 100.0
            
            # Check if this is a subscription type transaction (mock logic)
            # In reality, check 'FeeType' or similar field. 
            # We'll assume if description says subscription, we check amounts.
            if amount > EXPECTED_FEE:
                refund_amount = amount - EXPECTED_FEE
                formatted_refund = "{:.2f}".format(refund_amount)
                
                # Update Transaction
                update_transaction(transaction_id, 'Refunded', f"Auto-refunded excess amount: ${formatted_refund}")
                
                result = {
                    'status': 'Resolved',
                    'message': f"We detected an overcharge of ${formatted_refund} for your subscription. A refund of ${formatted_refund} has been automatically initiated. Status updated to 'Refunded'."
                }
            else:
                 result = {
                    'status': 'Escalated',
                    'message': "The charged amount appears to match the expected fee. We have escalated this to our billing team for a manual audit."
                }

        # --- Scenario 2: Status Verification (Not Reflected) ---
        elif "status" in description or "reflected" in description or "missing" in description:
             if txn_status == 'Pending':
                 txn_date_str = transaction.get('TransactionDate')
                 # Parse date "2026-01-05T16:30:00Z"
                 try:
                     # Handle simple ISO format
                     txn_date = datetime.strptime(txn_date_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                 except:
                     try:
                         txn_date = datetime.fromisoformat(txn_date_str)
                     except:
                         txn_date = datetime.now() 
                     
                 if datetime.now() - txn_date > timedelta(hours=24):
                     update_transaction(transaction_id, 'Escalated', "Escalated: Pending > 24h")
                     result = {
                         'status': 'Escalated',
                         'message': "Your transaction is still pending after 24 hours. We have escalated this to a Settlement Agent for immediate review."
                     }
                 else:
                     result = {
                         'status': 'Pending',
                         'message': "Your transaction is currently in 'Pending' state. It typically takes up to 24 hours to reflect. Please check back shortly."
                     }
             elif txn_status == 'Completed':
                  result = {
                     'status': 'Resolved',
                     'message': "Our records show this transaction was successfully Completed. Please check your relevant bank statement again."
                 }
             elif txn_status == 'Failed':
                  result = {
                      'status': 'Resolved',
                      'message': "This transaction failed. Please retry the payment."
                  }

        # --- Scenario 3: Failed Transaction (Retry) ---
        elif txn_status == 'Failed' or "failed" in description:
             message = transaction.get('ErrorMessage', 'Unknown error')
             
             # Check if eligible for retry (e.g., Network Error)
             if "network" in message.lower() or "timeout" in message.lower() or "failed" in description:
                 if retry_count < 2: # Max 2 retries
                     # Simulate Retry Success
                     new_retry_count = retry_count + 1
                     
                     # 50% chance success for simulation purposes, or force success as per requirements "If retry is successful..."
                     # Requirement says: "If the failure is due to temporary issue ... system will automatically retry"
                     # We'll simulate a successful retry for the demo flow.
                     update_transaction(transaction_id, 'Completed', f"Auto-retry successful (Attempt {new_retry_count})", new_retry_count)
                     
                     result = {
                         'status': 'Resolved',
                         'message': "It looks like the transaction failed due to a temporary network issue. We automatically retried the payment and it was Successful."
                     }
                 else:
                     update_transaction(transaction_id, 'Escalated', "Auto-retry failed (Max attempts)", retry_count)
                     result = {
                         'status': 'Escalated',
                         'message': "We attempted to retry the transaction but it failed again. This has been escalated to our technical support team."
                     }
             else:
                 # Non-retryable error (e.g. Insufficient Funds)
                 result = {
                     'status': 'Resolved',
                     'message': f"The transaction failed due to: {message}. Please verify your payment details and try again."
                 }
        
        else:
             # Default Fallback for Transaction category
             result = {
                'status': 'Escalated',
                'message': "We have received your transaction query. An agent has been assigned to review the details manually."
            }

        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
        
    except ClientError as e:
        print(f"DynamoDB Error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'status': 'Error', 'message': 'Internal system error accessing transaction records.'})
        }
    except Exception as e:
        print(f"Error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'status': 'Error', 'message': str(e)})
        }

def update_transaction(txn_id, new_status, notes, retry_count=None):
    try:
        update_expr = "set TransactionStatus=:s, ResolutionNotes=:n, LastUpdatedTimestamp=:t"
        expr_attrs = {
            ':s': new_status,
            ':n': notes,
            ':t': datetime.now().isoformat()
        }
        
        if retry_count is not None:
            update_expr += ", RetryCount=:r"
            expr_attrs[':r'] = retry_count
            
        table.update_item(
            Key={'TransactionID': txn_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_attrs
        )
    except Exception as e:
        print(f"Failed to update transaction: {e}")
