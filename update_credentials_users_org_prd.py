import boto3
import time
import json
from datetime import datetime, timedelta
from dateutil import parser

boto3.setup_default_session (profile_name='profile_name')
session = boto3.Session (profile_name='profile_name')

organizations = session.client ('organizations')
iam = boto3.client ('iam')
dynamodb = boto3.client ('dynamodb', region_name='us-east-1')

account_suspended = []
account_active = []

accounts = []
accounts_prd = []
another_accounts = []

def main():
    users = []
    accounts = []

    response = organizations.list_accounts ()
    accounts.extend (response['Accounts'])
    while 'NextToken' in response.keys ():
        response = organizations.list_accounts (NextToken=response['NextToken'])
        accounts.extend (response['Accounts'])
    print ('accounts found: ' + str (len (accounts)))


    for i in accounts:
        id_account = i['Id']
        status = i['Status']
        name = i['Name']
        if status == 'SUSPENDED':
            #Quando for comparar strings é bom normalizar antes (geralmente coloco pra minusculo) = Ficaria assim status.lower() == 'suspended'
            account_suspended.append ({"Name": name, "id_account": id_account, "Status": status})
        else:
            account_active.append ({"Name": name, "id_account": id_account, "Status": status})

    for contas in account_active:
        account_prd = contas['Name']
        if '-prd' in account_prd:
            accounts_prd.append (account_prd)
        else:
            another_accounts.append (account_prd)

    for account in account_active:
        print ()
        id_account_name = str(account['Name'])
        id_account_id = str(account['id_account'])
        print ('Account:', account['Name'], 'Id:', account['id_account'])

        credentials = session.get_credentials ()
        sts = session.client ('sts')
        assume_role_response = sts.assume_role (
            RoleArn=f"arn:aws:iam:XXXXXXXXXXX:{account['id_account']}:role/role_name",
            RoleSessionName="AssumeRoleSession1"
        )
        temp_session = boto3.Session (
            aws_access_key_id=assume_role_response['Credentials']['AccessKeyId'],
            aws_secret_access_key=assume_role_response['Credentials']['SecretAccessKey'],
            aws_session_token=assume_role_response['Credentials']['SessionToken']
        )
        iam = temp_session.client ('iam')

        response = iam.list_users ()
        users = response['Users']
        while 'Marker' in response.keys ():
            response = iam.list_users (Marker=response['Marker'])
            users.extend (response['Users'])
        print (f'Total usuários: {len (users)}')

        # Update the credentials for each user
        for user in users:
            username = user.get('UserName')

            response = iam.list_user_tags (UserName=username)
            tags = response.get ('Tags')
            if tags:
                job_status = [tag.get ('Value') for tag in tags if tag.get ('Key') == 'job_status']
                if job_status and job_status[0] == 'ativo':

                    # Get the current access keys
                    response = iam.list_access_keys (UserName=username)
                    access_keys = response.get ('AccessKeyMetadata')
                    if len (access_keys) >= 2:
                        creation_date1 = access_keys[0].get ('CreateDate')
                        creation_date2 = access_keys[1].get ('CreateDate')
                        if creation_date1>creation_date2:
                            access_key_to_delete = creation_date2
                        else:
                            print (">>> Deu certo, credencial do user {} - {} - {} excluida com sucesso".format (username, access_keys, id_account_name))
                            access_key_to_delete = creation_date1
                        iam.delete_access_key (UserName=username, AccessKeyId=access_key_to_delete.get ('AccessKeyId'))

                    # Check if the access key is older than 90 days
                    for item in access_keys:
                        date_access = item['CreateDate'].strftime ('%Y-%m-%d')
                        access_key_id = item['AccessKeyId']

                        create_date = parser.parse(date_access) + timedelta(days=0)

                        # if create_date > datetime.now():
                        if datetime.now() > create_date:
                            iam.update_access_key (UserName=username, AccessKeyId=access_key_id, Status='Inactive')
                            iam.delete_access_key (UserName=username, AccessKeyId=access_key_id)
                            print ("Deu certo, credencial do user {} - {} - {} inativada/excluida com sucesso".format (username, access_key_id, id_account_name))
                            print ()
                            response = iam.create_access_key (UserName=username)
                            new_access_key = response.get ('AccessKey')
                            new_access_key_id = new_access_key.get ('AccessKeyId')
                            new_secret_access_key = new_access_key.get ('SecretAccessKey')
                            credentials_user_date = new_access_key['CreateDate'].strftime ("%d-%m-%Y")
                            print ("Nova credencial do user {} - access_key {} - {} criada com sucesso".format (username, new_access_key_id, id_account_name))
                            print ()

                            # Store the new access key in Secrets Manager
                            kwargs = {'service_name': 'secretsmanager'}
                            if account['Name'] in accounts_prd:
                                kwargs.update ({'region_name': 'sa-east-1'})
                                secret = temp_session.client (**kwargs)
                                try:
                                    response = secret.get_secret_value (
                                        SecretId=username
                                    )
                                    print ("Secret {} already exists. Updating values...".format (username))
                                    secret.update_secret (
                                        SecretId=username,
                                        SecretString=json.dumps ({'access_key_id': new_access_key_id,
                                                                  'secret_access_key': new_secret_access_key}))
                                except secret.exceptions.ResourceNotFoundException:
                                    print ("Secret {} not found. Creating...".format (username))
                                    secret.create_secret (
                                        Name=username,
                                        SecretString=json.dumps ({'access_key_id': new_access_key_id,
                                                                  'secret_access_key': new_secret_access_key}))
                            else:
                                kwargs.update ({'region_name': 'us-east-1'})
                                secret = temp_session.client (**kwargs)
                                try:
                                    response = secret.get_secret_value (
                                        SecretId=username
                                    )
                                    print ("Secret {} already exists. Updating values...".format (username))
                                    secret.update_secret (
                                        SecretId=username,
                                        SecretString=json.dumps ({'access_key_id': new_access_key_id,
                                                                  'secret_access_key': new_secret_access_key}))
                                except secret.exceptions.ResourceNotFoundException:
                                    print ("Secret {} not found. Creating...".format (username))
                                    secret.create_secret (
                                        Name=username,
                                        SecretString=json.dumps ({'access_key_id': new_access_key_id,
                                                                  'secret_access_key': new_secret_access_key}))

                            sts = session.client ('sts')
                            assume_role_response = sts.assume_role (
                                RoleArn="arn:aws:iam::XXXXXXXXXXX:role/role_name",
                                RoleSessionName="AssumeRoleSession1")
                            temp_session = boto3.Session (
                                aws_access_key_id=assume_role_response['Credentials']['AccessKeyId'],
                                aws_secret_access_key=assume_role_response['Credentials']['SecretAccessKey'],
                                aws_session_token=assume_role_response['Credentials']['SessionToken'])

                            kwargs = {'service_name': 'dynamodb', 'region_name': 'sa-east-1'}
                            dynamodb = temp_session.client (**kwargs)

                            table = 'infosec_iam_user'
                            item = {
                                'username': {
                                    'S': username
                                },
                                'access_key': {
                                    'S': new_access_key_id
                                },
                                'access_key_old': {
                                    'S': access_key_id
                                },
                                'last_date_update': {
                                    'S': credentials_user_date
                                },
                                'account_name': {
                                    'S': id_account_name
                                },
                                'account_id': {
                                    'S': id_account_id
                                },
                                'status_credentials': {
                                    'S': status
                                }
                            }
                            response = dynamodb.put_item (TableName=table, Item=item)
                            print("A nova credencial do {} - {} - {} foi criada com sucesso na tabela 'infosec_iam_user' ".format(username, new_access_key_id, id_account_name))

                            table = 'infosec_iam_user'
                            Key = {
                                'username': {
                                    'S': username
                                },
                                'access_key': {
                                    'S': access_key_id
                                }
                            }
                            response = dynamodb.delete_item (TableName=table, Key=Key)
                            print ("A credencial antiga do usuário {} - {} - {} foi excluida com sucesso na tabela 'infosec_iam_user'".format(username, access_key_id, id_account_name))
                            print ()
                        else:
                            print ("A credencial do user {} - {} - {} ainda está no prazo de 90 dias".format (username, access_key_id, id_account_name))

            else:
                print ("User {} - {} doesn't have the required tag".format (username, id_account_name))

    print ()

main ()
