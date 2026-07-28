import os
import asyncio
from metaapi_cloud_sdk import MetaApi
from datetime import datetime, timedelta

# Note: for information on how to use this example code please read https://metaapi.cloud/docs/client/usingCodeExamples

token = os.getenv('TOKEN') or 'eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiI4ZDE0NzRjN2JhNTYyMGZhOThhY2E0ODE0ZTM1ZmZhZCIsImFjY2Vzc1J1bGVzIjpbeyJpZCI6InRyYWRpbmctYWNjb3VudC1tYW5hZ2VtZW50LWFwaSIsIm1ldGhvZHMiOlsidHJhZGluZy1hY2NvdW50LW1hbmFnZW1lbnQtYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbImFjY291bnQ6JFVTRVJfSUQkOjNiODRlYjU4LTlhZWUtNDhiNi05YjYzLTAwZDEzZWVmZDc5NyJdfSx7ImlkIjoibWV0YWFwaS1yZXN0LWFwaSIsIm1ldGhvZHMiOlsibWV0YWFwaS1hcGk6cmVzdDpwdWJsaWM6KjoqIl0sInJvbGVzIjpbInJlYWRlciIsIndyaXRlciJdLCJyZXNvdXJjZXMiOlsiYWNjb3VudDokVVNFUl9JRCQ6M2I4NGViNTgtOWFlZS00OGI2LTliNjMtMDBkMTNlZWZkNzk3Il19LHsiaWQiOiJtZXRhYXBpLXJwYy1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOndzOnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyJhY2NvdW50OiRVU0VSX0lEJDozYjg0ZWI1OC05YWVlLTQ4YjYtOWI2My0wMGQxM2VlZmQ3OTciXX0seyJpZCI6Im1ldGFhcGktcmVhbC10aW1lLXN0cmVhbWluZy1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOndzOnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyJhY2NvdW50OiRVU0VSX0lEJDozYjg0ZWI1OC05YWVlLTQ4YjYtOWI2My0wMGQxM2VlZmQ3OTciXX0seyJpZCI6Im1ldGFzdGF0cy1hcGkiLCJtZXRob2RzIjpbIm1ldGFzdGF0cy1hcGk6cmVzdDpwdWJsaWM6KjoqIl0sInJvbGVzIjpbInJlYWRlciJdLCJyZXNvdXJjZXMiOlsiYWNjb3VudDokVVNFUl9JRCQ6M2I4NGViNTgtOWFlZS00OGI2LTliNjMtMDBkMTNlZWZkNzk3Il19LHsiaWQiOiJyaXNrLW1hbmFnZW1lbnQtYXBpIiwibWV0aG9kcyI6WyJyaXNrLW1hbmFnZW1lbnQtYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbImFjY291bnQ6JFVTRVJfSUQkOjNiODRlYjU4LTlhZWUtNDhiNi05YjYzLTAwZDEzZWVmZDc5NyJdfSx7ImlkIjoibWV0YWFwaS1yZWFsLXRpbWUtc3RyZWFtaW5nLWFwaSIsIm1ldGhvZHMiOlsibWV0YWFwaS1hcGk6d3M6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbImFjY291bnQ6JFVTRVJfSUQkOjNiODRlYjU4LTlhZWUtNDhiNi05YjYzLTAwZDEzZWVmZDc5NyJdfSx7ImlkIjoiY29weWZhY3RvcnktYXBpIiwibWV0aG9kcyI6WyJjb3B5ZmFjdG9yeS1hcGk6cmVzdDpwdWJsaWM6KjoqIl0sInJvbGVzIjpbInJlYWRlciIsIndyaXRlciJdLCJyZXNvdXJjZXMiOlsiYWNjb3VudDokVVNFUl9JRCQ6M2I4NGViNTgtOWFlZS00OGI2LTliNjMtMDBkMTNlZWZkNzk3Il19XSwiaWdub3JlUmF0ZUxpbWl0cyI6ZmFsc2UsInRva2VuSWQiOiIyMDIxMDIxMyIsImltcGVyc29uYXRlZCI6ZmFsc2UsInJlYWxVc2VySWQiOiI4ZDE0NzRjN2JhNTYyMGZhOThhY2E0ODE0ZTM1ZmZhZCIsImlhdCI6MTc4NTE4NTI5OSwiZXhwIjoxNzg3Nzc3Mjk5fQ.cIinKUrM0CABMCsr8IaMOqzADMs9BnVv6p3Fv61ED9rMI5N5Bllpc5MoO7_1kzYvNf0fSYdMtHnisy4G2QLuY-in0WUMByUBYPSAAUvaympGM9qCQCDGF1d3IoghpRZyKoiJoqcdHox6XDVG10yKjJQ5_ScYZu9jX1WtPQlvYNg70zuBRbGmFyiQKkO6lO-hJLeevWLDY7oleDWXcH-1PPn9shnvRIqiRUsK9TJOwRns84RxJKfmTerQcJJy8X38s8YXkryiYs0xVMZ2gBxCUfeguT7I8tPRi2uePf18UiV0SOU0_7jOFlwCnaWMCwiwRQIIQZW_Jpx1lC1AaFsguc8SKwsUUIgDVV29vy-r-_9dmFfQ1cGpm93ly7U_xVAhHLtFvyqM3Wk5SC1juw3-s4OOHOjhfwEvyqF9zz9r9pyA_IcDXDeBMVm0QIgrUpTiuaFRmKcKohvNFmJOpBqk2Mbd-a2P9o6OBbj-4aM9OEKgXfXJgw22BAWiEuteXREzjFe2XLacY-lHkPeuFmVsza39q0xeyw2Ddo6hhRMu3AgUn8Dqis7qhtXL7t5Uuncg1EZqsfDCQWLPE1OxWHxW_0L-pOthSjxY6bBQNYs9swWOKSJwmGiTHMFq3jVPhecaJp4Ginh0Ni71kxRz-nh13RGXWJFchgzdQ1wftYfrli4'
accountId = os.getenv('ACCOUNT_ID') or '3b84eb58-9aee-48b6-9b63-00d13eefd797'


async def test_meta_api_synchronization():
    api = MetaApi(token)
    try:
        account = await api.metatrader_account_api.get_account(accountId)
        initial_state = account.state
        deployed_states = ['DEPLOYING', 'DEPLOYED']

        if initial_state not in deployed_states:
            #  wait until account is deployed and connected to broker
            print('Deploying account')
            await account.deploy()

        print('Waiting for API server to connect to broker (may take couple of minutes)')
        await account.wait_connected()

        # connect to MetaApi API
        connection = account.get_rpc_connection()
        await connection.connect()

        # wait until terminal state synchronized to the local state
        print('Waiting for SDK to synchronize to terminal state (may take some time depending on your history size)')
        await connection.wait_synchronized()

        # invoke RPC API (replace ticket numbers with actual ticket numbers which exist in your MT account)
        print('Testing MetaAPI RPC API')
        print('account information:', await connection.get_account_information())
        print('positions:', await connection.get_positions())
        # print(await connection.get_position('1234567'))
        print('open orders:', await connection.get_orders())
        # print(await connection.get_order('1234567'))
        print('history orders by ticket:', await connection.get_history_orders_by_ticket('1234567'))
        print('history orders by position:', await connection.get_history_orders_by_position('1234567'))
        print(
            'history orders (~last 3 months):',
            await connection.get_history_orders_by_time_range(
                datetime.utcnow() - timedelta(days=90), datetime.utcnow()
            ),
        )
        print('history deals by ticket:', await connection.get_deals_by_ticket('1234567'))
        print('history deals by position:', await connection.get_deals_by_position('1234567'))
        print(
            'history deals (~last 3 months):',
            await connection.get_deals_by_time_range(datetime.utcnow() - timedelta(days=90), datetime.utcnow()),
        )
        print('server time', await connection.get_server_time())

        # calculate margin required for trade
        print(
            'margin required for trade',
            await connection.calculate_margin(
                {'symbol': 'GBPUSD', 'type': 'ORDER_TYPE_BUY', 'volume': 0.1, 'openPrice': 1.1}
            ),
        )

        # trade
        print('Submitting pending order')
        try:
            result = await connection.create_limit_buy_order(
                'GBPUSD', 0.07, 1.0, 0.9, 2.0, {
                    'comment': 'comm',
                    'clientId': 'TE_GBPUSD_7hyINWqAlE',
                    'expiration': {
                        'type': 'ORDER_TIME_SPECIFIED',
                        'time': datetime.now() + timedelta(days=1)
                    }
                }
            )
            print('Trade successful, result code is ' + result['stringCode'])
        except Exception as err:
            print('Trade failed with error:')
            print(api.format_error(err))
        if initial_state not in deployed_states:
            # undeploy account if it was undeployed
            print('Undeploying account')
            await connection.close()
            await account.undeploy()

    except Exception as err:
        print(api.format_error(err))
    exit()


asyncio.run(test_meta_api_synchronization())
