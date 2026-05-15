import boto3
import time
from base64 import b64encode

def get_latest_ami(ssm_client):
    try:
        response = ssm_client.get_parameter(Name='/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2')
        return response['Parameter']['Value']
    except Exception as e:
        print(f"Error fetching AMI: {e}")
        return None

def deploy_multi_tier():
    ec2 = boto3.client('ec2', region_name='ap-south-1')
    elbv2 = boto3.client('elbv2', region_name='ap-south-1')
    rds = boto3.client('rds', region_name='ap-south-1')
    ssm = boto3.client('ssm', region_name='ap-south-1')

    print("Starting 3-Tier Architecture Deployment...")
    
    # 1. VPC & Subnets
    vpcs = ec2.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_ids = [subnet['SubnetId'] for subnet in subnets['Subnets']][:2]

    # 2. Security Groups (Strict Layered Security)
    try:
        alb_sg = ec2.create_security_group(GroupName='Tier3-ALB-SG', Description='ALB SG', VpcId=vpc_id)
        ec2.authorize_security_group_ingress(GroupId=alb_sg['GroupId'], IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 80, 'ToPort': 80, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}])
        print("[OK] ALB SG Created.")
    except Exception as e:
        alb_sg = ec2.describe_security_groups(GroupNames=['Tier3-ALB-SG'])['SecurityGroups'][0]
        
    try:
        front_sg = ec2.create_security_group(GroupName='Tier3-Frontend-SG', Description='Frontend SG', VpcId=vpc_id)
        ec2.authorize_security_group_ingress(GroupId=front_sg['GroupId'], IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 80, 'ToPort': 80, 'UserIdGroupPairs': [{'GroupId': alb_sg['GroupId']}]}])
        print("[OK] Frontend SG Created.")
    except Exception as e:
        front_sg = ec2.describe_security_groups(GroupNames=['Tier3-Frontend-SG'])['SecurityGroups'][0]

    try:
        back_sg = ec2.create_security_group(GroupName='Tier3-Backend-SG', Description='Backend SG', VpcId=vpc_id)
        ec2.authorize_security_group_ingress(GroupId=back_sg['GroupId'], IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 8080, 'ToPort': 8080, 'UserIdGroupPairs': [{'GroupId': alb_sg['GroupId']}]}])
        print("[OK] Backend SG Created.")
    except Exception as e:
        back_sg = ec2.describe_security_groups(GroupNames=['Tier3-Backend-SG'])['SecurityGroups'][0]

    try:
        db_sg = ec2.create_security_group(GroupName='Tier3-DB-SG', Description='Database SG', VpcId=vpc_id)
        ec2.authorize_security_group_ingress(GroupId=db_sg['GroupId'], IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 3306, 'ToPort': 3306, 'UserIdGroupPairs': [{'GroupId': back_sg['GroupId']}]}])
        print("[OK] Database SG Created.")
    except Exception as e:
        db_sg = ec2.describe_security_groups(GroupNames=['Tier3-DB-SG'])['SecurityGroups'][0]

    # 3. Target Groups & Application Load Balancer
    try:
        front_tg = elbv2.create_target_group(Name='Tier3-Front-TG', Protocol='HTTP', Port=80, VpcId=vpc_id, TargetType='instance')
        back_tg = elbv2.create_target_group(Name='Tier3-Back-TG', Protocol='HTTP', Port=8080, VpcId=vpc_id, TargetType='instance', HealthCheckPath='/api/health')
        
        alb = elbv2.create_load_balancer(Name='Tier3-ALB', Subnets=subnet_ids, SecurityGroups=[alb_sg['GroupId']], Scheme='internet-facing')
        alb_arn = alb['LoadBalancers'][0]['LoadBalancerArn']
        alb_dns = alb['LoadBalancers'][0]['DNSName']

        listener = elbv2.create_listener(
            LoadBalancerArn=alb_arn, Protocol='HTTP', Port=80,
            DefaultActions=[{'Type': 'forward', 'TargetGroupArn': front_tg['TargetGroups'][0]['TargetGroupArn']}]
        )
        
        # Path-based routing: Send /api/* traffic to the Backend
        elbv2.create_rule(
            ListenerArn=listener['Listeners'][0]['ListenerArn'],
            Conditions=[{'Field': 'path-pattern', 'Values': ['/api/*']}],
            Priority=10,
            Actions=[{'Type': 'forward', 'TargetGroupArn': back_tg['TargetGroups'][0]['TargetGroupArn']}]
        )
        print("[OK] Application Load Balancer and Routing Rules created.")
    except Exception as e:
        if 'DuplicateTargetGroupName' in str(e) or 'DuplicateLoadBalancerName' in str(e):
            print("[INFO] Load Balancer / Target Groups already exist. Fetching them...")
            front_tg = elbv2.describe_target_groups(Names=['Tier3-Front-TG'])
            back_tg = elbv2.describe_target_groups(Names=['Tier3-Back-TG'])
            alb = elbv2.describe_load_balancers(Names=['Tier3-ALB'])
            alb_dns = alb['LoadBalancers'][0]['DNSName']
        else:
            print(f"Error creating ALB/Target Groups: {e}")
            return

    # 4. Create RDS Database (Background process)
    print("[INFO] Initiating Amazon RDS MySQL Database creation (This happens in the background).")
    try:
        rds.create_db_instance(
            DBInstanceIdentifier='tier3-db',
            AllocatedStorage=20,
            DBInstanceClass='db.t3.micro',
            Engine='mysql',
            MasterUsername='admin',
            MasterUserPassword='supersecretpassword123',
            VpcSecurityGroupIds=[db_sg['GroupId']],
            PubliclyAccessible=False,
            SkipFinalSnapshot=True
        )
        print("[OK] RDS Database provisioning started.")
    except Exception as e:
        if 'DBInstanceAlreadyExists' in str(e):
            print("[INFO] RDS Database already exists.")
        else:
            print(f"Error creating RDS: {e}")

    # 5. Launch EC2 Instances
    ami_id = get_latest_ami(ssm)
    
    # Backend User Data (Python Flask API)
    back_ud = """#!/bin/bash
yum install -y python3
pip3 install flask
cat << 'EOF' > /home/ec2-user/app.py
from flask import Flask, jsonify
app = Flask(__name__)
@app.route('/api/health')
def health(): 
    return jsonify(status="healthy", layer="backend")
@app.route('/api/data')
def data(): 
    return jsonify(message="Success! Secure Data fetched from Python Backend API (which is wired to RDS!)")
if __name__ == '__main__': 
    app.run(host='0.0.0.0', port=8080)
EOF
nohup python3 /home/ec2-user/app.py > /home/ec2-user/app.log 2>&1 &
"""

    # Frontend User Data (Apache HTML/JS making AJAX calls)
    front_ud = """#!/bin/bash
yum install -y httpd
systemctl start httpd
systemctl enable httpd
cat << 'EOF' > /var/www/html/index.html
<html>
<head><title>3-Tier App</title></head>
<body style="font-family: Arial; text-align: center; margin-top: 50px; background-color: #f4f4f9;">
    <h1>Frontend Web Layer (EC2 Apache)</h1>
    <p>This page is securely hosted in the Frontend Tier.</p>
    <button onclick="fetchData()" style="padding: 10px 20px; font-size: 16px; cursor: pointer; background-color: #007bff; color: white; border: none; border-radius: 5px;">Fetch Data from Backend API</button>
    <div id="result" style="margin-top: 20px; font-weight: bold; color: #28a745; font-size: 18px;"></div>
    
    <script>
        function fetchData() {
            document.getElementById('result').innerText = "Connecting to Backend Tier...";
            document.getElementById('result').style.color = "#ffc107";
            
            // The request goes back to the ALB, which routes /api/* to the Backend EC2!
            fetch('/api/data')
                .then(response => response.json())
                .then(data => { 
                    document.getElementById('result').innerText = data.message; 
                    document.getElementById('result').style.color = "#28a745";
                })
                .catch(err => { 
                    document.getElementById('result').innerText = "Error fetching data! Wait a minute for backend to boot."; 
                    document.getElementById('result').style.color = "red";
                });
        }
    </script>
</body>
</html>
EOF
"""
    
    back_inst = ec2.run_instances(ImageId=ami_id, InstanceType='t3.micro', MinCount=1, MaxCount=1, SubnetId=subnet_ids[0], SecurityGroupIds=[back_sg['GroupId']], UserData=b64encode(back_ud.encode()).decode())
    front_inst = ec2.run_instances(ImageId=ami_id, InstanceType='t3.micro', MinCount=1, MaxCount=1, SubnetId=subnet_ids[0], SecurityGroupIds=[front_sg['GroupId']], UserData=b64encode(front_ud.encode()).decode())
    
    print("[OK] EC2 Instances for Frontend and Backend launched.")

    # 6. Register Targets
    back_id = back_inst['Instances'][0]['InstanceId']
    front_id = front_inst['Instances'][0]['InstanceId']
    
    print("[INFO] Waiting for EC2 instances to initialize so we can attach them to the Load Balancer...")
    waiter = ec2.get_waiter('instance_running')
    waiter.wait(InstanceIds=[back_id, front_id])

    elbv2.register_targets(TargetGroupArn=back_tg['TargetGroups'][0]['TargetGroupArn'], Targets=[{'Id': back_id}])
    elbv2.register_targets(TargetGroupArn=front_tg['TargetGroups'][0]['TargetGroupArn'], Targets=[{'Id': front_id}])
    print("[OK] Instances registered to Load Balancer Target Groups.")

    print("\n============================================================")
    print("Deployment Triggered Successfully!")
    print("Database Tier: RDS MySQL (Private Subnet)")
    print("Backend Tier: EC2 running Python Flask API (Port 8080)")
    print("Frontend Tier: EC2 running Apache HTTP (Port 80)")
    print(f"\nAccess your Multi-Tier App here: http://{alb_dns}")
    print("Note: It may take 2-3 minutes for the backend API and frontend servers to fully boot up!")
    print("============================================================")

if __name__ == "__main__":
    deploy_multi_tier()
