# AWS-Project-3
Multi-tier Web App Deployment
This project deploys a highly secure, logically separated 3-Tier Web Application on AWS using Boto3.

Architecture Layers
Frontend Tier (Web Layer)

Hosted on an EC2 instance running an Apache HTTP server.
Handles the static HTML/JS rendering.
Strictly protected by a Security Group (Tier3-Frontend-SG) that only accepts traffic from the Application Load Balancer.
Backend Tier (App Layer)

Hosted on an EC2 instance running a lightweight Python Flask API.
Listens on port 8080.
Strictly protected by a Security Group (Tier3-Backend-SG) that only accepts traffic routed from the ALB under the /api/* path.
Database Tier (Data Layer)

An Amazon RDS MySQL Database.
Isolated completely from the internet.
Protected by a Security Group (Tier3-DB-SG) that only accepts port 3306 traffic from the Backend EC2 instances.
Application Load Balancer (ALB)

Acts as the single entry point for all global traffic.
Uses Path-Based Routing:
Traffic hitting / is routed to the Frontend Target Group.
Traffic hitting /api/* is routed directly to the Backend Target Group.
Getting Started
Deploy the entire infrastructure stack:

python deploy_infrastructure.py
Wait about 3 minutes for all systems to initialize. Open the Load Balancer URL in your browser. You will see the Frontend interface. Click the button to watch the Frontend execute a Javascript fetch request seamlessly through the ALB into the Backend API layer!
