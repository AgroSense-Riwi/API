#!/usr/bin/env python3
"""AgroSense Café — FinOps, CT-03 Tagging, CT-09 Live Alarms & Teardown CLI (boto3).

Provides zero-dependency (boto3-only) execution for:
  - nat-toggle.sh       (status | up | down)
  - setup-alarms-ct09.sh (status | trigger-demo | reset-demo)
  - teardown.sh         (verify | pause | destroy)
"""

from __future__ import annotations

import os
import sys
import time
import boto3

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REGION = os.getenv("AWS_REGION", "us-east-2")
PROJECT_NAME = os.getenv("PROJECT_NAME", "agrosense")
CLUSTER = os.getenv("CLUSTER", "agrosense-cluster")

STD_TAGS = [
    {"Key": "proyecto", "Value": "AgroSense-Cafe"},
    {"Key": "ambiente", "Value": "production"},
    {"Key": "responsable", "Value": "AgroSense-Team"},
    {"Key": "Project", "Value": "AgroSense-Cafe"},
    {"Key": "Environment", "Value": "production"},
    {"Key": "ManagedBy", "Value": "IaC"},
    {"Key": "Owner", "Value": "Keinerdmb"},
]


def cmd_nat(action: str) -> None:
    ec2 = boto3.client("ec2", region_name=REGION)
    if action == "status":
        print(f"=== Estado actual de NAT Gateway en {REGION} ===")
        nats = ec2.describe_nat_gateways(
            Filters=[{"Name": "state", "Values": ["available", "pending", "deleting"]}]
        )["NatGateways"]
        if not nats:
            print("  No hay NAT Gateways activos ($0.00/h).")
            return
        for n in nats:
            addr = (n.get("NatGatewayAddresses") or [{}])[0]
            print(
                f"  ID: {n['NatGatewayId']} | State: {n['State']} | Subnet: {n.get('SubnetId')} "
                f"| PublicIp: {addr.get('PublicIp')} | EIP: {addr.get('AllocationId')}"
            )
    elif action == "down":
        print(f"=== Apagando NAT Gateway y liberando Elastic IP ({REGION}) ===")
        nats = ec2.describe_nat_gateways(
            Filters=[{"Name": "state", "Values": ["available", "pending"]}]
        )["NatGateways"]
        if not nats:
            print("  ↳ No hay NAT Gateways activos.")
        for n in nats:
            nat_id = n["NatGatewayId"]
            eips = [a["AllocationId"] for a in n.get("NatGatewayAddresses", []) if a.get("AllocationId")]
            print(f"  ↳ Eliminando NAT Gateway {nat_id}...")
            ec2.delete_nat_gateway(NatGatewayId=nat_id)
            for _ in range(30):
                st = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])["NatGateways"][0]["State"]
                if st == "deleted":
                    break
                time.sleep(3)
            for alloc_id in eips:
                try:
                    ec2.release_address(AllocationId=alloc_id)
                    print(f"  ↳ Elastic IP {alloc_id} liberada.")
                except Exception as exc:
                    print(f"  ↳ Aviso al liberar {alloc_id}: {exc}")
        print("=== ✅ NAT Gateway apagado ($0.00/h en NAT/EIP) ===")
    elif action == "up":
        print("=== Encendiendo NAT Gateway + Elastic IP y enrutando subredes privadas ===")
        nats = ec2.describe_nat_gateways(
            Filters=[
                {"Name": "tag:Name", "Values": [f"{PROJECT_NAME}-nat"]},
                {"Name": "state", "Values": ["available", "pending"]},
            ]
        )["NatGateways"]
        if nats:
            nat_id = nats[0]["NatGatewayId"]
            print(f"  ↳ NAT Gateway ya existe: {nat_id}")
        else:
            pub_sub = ec2.describe_subnets(
                Filters=[{"Name": "tag:Name", "Values": [f"{PROJECT_NAME}-public-1"]}]
            )["Subnets"][0]["SubnetId"]
            eip = ec2.allocate_address(
                Domain="vpc",
                TagSpecifications=[
                    {
                        "ResourceType": "elastic-ip",
                        "Tags": STD_TAGS + [{"Key": "Name", "Value": f"{PROJECT_NAME}-nat-eip"}],
                    }
                ],
            )
            nat = ec2.create_nat_gateway(
                SubnetId=pub_sub,
                AllocationId=eip["AllocationId"],
                TagSpecifications=[
                    {
                        "ResourceType": "natgateway",
                        "Tags": STD_TAGS + [{"Key": "Name", "Value": f"{PROJECT_NAME}-nat"}],
                    }
                ],
            )
            nat_id = nat["NatGateway"]["NatGatewayId"]
            print(f"  ↳ Esperando disponibilidad de {nat_id}...")
            ec2.get_waiter("nat_gateway_available").wait(NatGatewayIds=[nat_id])

        priv_rt = ec2.describe_route_tables(
            Filters=[{"Name": "tag:Name", "Values": [f"{PROJECT_NAME}-private-rt"]}]
        )["RouteTables"][0]["RouteTableId"]
        try:
            ec2.replace_route(
                RouteTableId=priv_rt, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat_id
            )
        except Exception:
            ec2.create_route(
                RouteTableId=priv_rt, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat_id
            )
        print(f"=== ✅ NAT Gateway {nat_id} activo y enrutado en {priv_rt} ===")
    else:
        raise SystemExit(f"Acción inválida para nat: {action}")


def cmd_alarms(action: str) -> None:
    cw = boto3.client("cloudwatch", region_name=REGION)
    alarm_name = os.getenv("ALARM_NAME", "agrosense-dlq-messages-alarm")
    alarm_names = [
        "agrosense-api-alb-5xx-alarm",
        "agrosense-api-unhealthy-hosts-alarm",
        "agrosense-dlq-messages-alarm",
    ]
    if action == "status":
        print("=== Estado de Alarmas Operativas CloudWatch (CT-09) ===")
        for a in cw.describe_alarms(AlarmNames=alarm_names)["MetricAlarms"]:
            print(
                f"  - {a['AlarmName']:36s} | State: {a['StateValue']:17s} "
                f"| Metric: {a['Namespace']}/{a['MetricName']} >= {a['Threshold']}"
            )
    elif action == "trigger-demo":
        print("=== 🚨 Induciendo fallo controlado para Demo en Vivo (CT-09) ===")
        cw.put_metric_data(
            Namespace="AgroSense/Messaging",
            MetricData=[
                {
                    "MetricName": "DLQMessageCount",
                    "Dimensions": [{"Name": "QueueName", "Value": "telemetry.dlq.queue"}],
                    "Value": 5.0,
                    "Unit": "Count",
                }
            ],
        )
        cw.set_alarm_state(
            AlarmName=alarm_name,
            StateValue="ALARM",
            StateReason=(
                "CT-09 Live Demo: Controlled fault injection detected 5 unprocessable "
                "telemetry payloads in telemetry.dlq.queue (>= 1 threshold)"
            ),
        )
        cmd_alarms("status")
    elif action == "reset-demo":
        print(f"=== ✅ Restaurando alarma '{alarm_name}' a estado OK ===")
        cw.put_metric_data(
            Namespace="AgroSense/Messaging",
            MetricData=[
                {
                    "MetricName": "DLQMessageCount",
                    "Dimensions": [{"Name": "QueueName", "Value": "telemetry.dlq.queue"}],
                    "Value": 0.0,
                    "Unit": "Count",
                }
            ],
        )
        cw.set_alarm_state(
            AlarmName=alarm_name,
            StateValue="OK",
            StateReason="CT-09 Live Demo resolved: DLQ drained and telemetry consumer nominal (DLQMessageCount = 0)",
        )
        cmd_alarms("status")
    else:
        raise SystemExit(f"Acción inválida para alarms: {action}")


def cmd_teardown(mode: str) -> None:
    ec2 = boto3.client("ec2", region_name=REGION)
    ecs = boto3.client("ecs", region_name=REGION)
    elbv2 = boto3.client("elbv2", region_name=REGION)
    efs = boto3.client("efs", region_name=REGION)
    tag_api = boto3.client("resourcegroupstaggingapi", region_name=REGION)

    if mode == "verify":
        print("============================================================")
        print(f" AgroSense Café — Auditoría de Recursos Facturables ({REGION})")
        print("============================================================")
        print("[1/7] Instancias EC2:")
        for res in ec2.describe_instances()["Reservations"]:
            for i in res["Instances"]:
                name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "-")
                print(f"  {i['InstanceId']} | {i['InstanceType']} | {i['State']['Name']} | {name}")

        print(f"[2/7] Servicios ECS ({CLUSTER}):")
        svcs = ["agrosense-api-service", "agrosense-broker", "agrosense-airflow"]
        for s in ecs.describe_services(cluster=CLUSTER, services=svcs).get("services", []):
            print(
                f"  {s['serviceName']:24s} | Status: {s['status']} "
                f"| Desired: {s['desiredCount']} | Running: {s['runningCount']}"
            )

        print("[3/7] NAT Gateways activos:")
        nats = ec2.describe_nat_gateways(
            Filters=[{"Name": "state", "Values": ["available", "pending"]}]
        )["NatGateways"]
        print(f"  Activos: {len(nats)}")

        print("[4/7] Elastic IPs asignadas:")
        for addr in ec2.describe_addresses()["Addresses"]:
            print(
                f"  {addr['AllocationId']} | {addr.get('PublicIp')} "
                f"| ServiceManaged: {addr.get('ServiceManaged', 'user-allocated')}"
            )

        print("[5/7] Load Balancers (ALB):")
        for lb in elbv2.describe_load_balancers()["LoadBalancers"]:
            print(f"  {lb['LoadBalancerName']} | {lb['State']['Code']} | {lb['DNSName']}")

        print("[6/7] Sistemas de archivos EFS:")
        for fs in efs.describe_file_systems()["FileSystems"]:
            print(f"  {fs['FileSystemId']} | {fs['LifeCycleState']} | {fs['SizeInBytes']['Value']} B")

        print("[7/7] Verificación de Tags Obligatorios CT-03 (proyecto, ambiente, responsable):")
        untagged = []
        total = 0
        for page in tag_api.get_paginator("get_resources").paginate():
            for m in page["ResourceTagMappingList"]:
                total += 1
                tmap = {t["Key"]: t["Value"] for t in m.get("Tags", [])}
                if not all(k in tmap for k in ("proyecto", "ambiente", "responsable")):
                    untagged.append(m["ResourceARN"])
        if not untagged:
            print(f"  ✅ 100% de recursos ({total}/{total}) etiquetados con (proyecto, ambiente, responsable).")
        else:
            print(f"  ⚠️ Recursos pendientes de tag CT-03: {len(untagged)}")

    elif mode == "pause":
        print("============================================================")
        print(" AgroSense Café — Modo PAUSE (Ahorro de costos entre sesiones)")
        print("============================================================")
        for svc in ("agrosense-api-service", "agrosense-broker", "agrosense-airflow"):
            try:
                ecs.update_service(cluster=CLUSTER, service=svc, desiredCount=0)
                print(f"  ↳ {svc} -> desiredCount=0")
            except Exception as exc:
                print(f"  ↳ {svc}: {exc}")

        running = [
            i["InstanceId"]
            for r in ec2.describe_instances(
                Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
            )["Reservations"]
            for i in r["Instances"]
        ]
        if running:
            ec2.stop_instances(InstanceIds=running)
            print(f"  ↳ EC2 detenidas: {running}")
        else:
            print("  ↳ Ninguna instancia EC2 en estado running.")

        cmd_nat("down")
        cmd_teardown("verify")

    elif mode == "destroy":
        if os.getenv("CONFIRM_DESTROY") != "yes":
            raise SystemExit(
                "❌ Para ejecutar el teardown destructivo post-sustentación, ejecuta:\n"
                "   CONFIRM_DESTROY=yes ./infra/aws/teardown.sh destroy"
            )
        cmd_teardown("pause")
    else:
        raise SystemExit(f"Modo inválido para teardown: {mode}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("Uso: aws_ops.py <nat|alarms|teardown> <action>")
    group, act = sys.argv[1], sys.argv[2]
    if group == "nat":
        cmd_nat(act)
    elif group == "alarms":
        cmd_alarms(act)
    elif group == "teardown":
        cmd_teardown(act)
    else:
        raise SystemExit(f"Subcomando desconocido: {group}")
