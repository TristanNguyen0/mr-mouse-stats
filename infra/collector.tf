# The always-on collector: Twitch IRC plus the timer-driven Liquipedia
# scrape, in one ARM64 Fargate task.
#
# Public subnet with a public IP and NO inbound rules, deliberately. The
# task only makes outbound connections (Twitch IRC, Liquipedia, Neon), and a
# private subnet would need a NAT Gateway at $32.85/month — over 3x the cost
# of the task it would serve.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "collector" {
  name        = "${local.name}-collector"
  description = "Outbound only; the collector never accepts connections"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound (Twitch IRC 6697, Liquipedia 443, Neon 5432)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # No ingress block at all: the ReadOnlyIrcClient cannot accept or send,
  # only connect out.
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_iam_role" "task_execution" {
  name = "${local.name}-task-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role pulls the secret at container start.
resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "read-database-dsn"
  role = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.database.arn
    }]
  })
}

resource "aws_iam_role" "task" {
  name = "${local.name}-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/ecs/${local.name}-collector"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "collector" {
  family                   = "${local.name}-collector"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.collector_cpu
  memory                   = var.collector_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    # ~20% cheaper than x86 and nothing in this codebase is
    # architecture-sensitive.
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "collector"
    image     = "${aws_ecr_repository.collector.repository_url}:latest"
    essential = true
    command   = ["serve"]

    environment = [
      { name = "TZ", value = "UTC" },
      { name = "MR_MOUSE_STATS_TOURNAMENTS", value = join(",", var.tournaments) },
      { name = "MR_MOUSE_STATS_SCRAPE_INTERVAL", value = tostring(var.scrape_interval_seconds) },
      # /tmp is writable and survives for the life of the task, which is
      # what the 24h HTTP cache TTL needs.
      { name = "MR_MOUSE_STATS_CACHE_DIR", value = "/tmp/liquipedia-cache" },
    ]

    secrets = [
      { name = "MR_MOUSE_STATS_DB", valueFrom = aws_secretsmanager_secret.database.arn }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.collector.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "collector"
      }
    }

    # service.py installs a SIGTERM handler that drains the capture spool.
    stopTimeout = 30
  }])
}

resource "aws_ecs_service" "collector" {
  name            = "${local.name}-collector"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.collector.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # Exactly one collector. Two would double-join every channel and race on
  # the same rate gate; twitch_messages dedupes on msg_id so it would not
  # corrupt data, but it doubles Liquipedia traffic for nothing.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.collector.id]
    assign_public_ip = true # avoids a NAT Gateway; see the header comment
  }
}

# The collector going quiet looks identical to a quiet chat from outside.
# channel_join_status is written after the join grace period, so a task that
# stops running is the signal worth alarming on.
resource "aws_cloudwatch_metric_alarm" "collector_stopped" {
  alarm_name          = "${local.name}-collector-not-running"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "RunningTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = 300
  statistic           = "Average"
  threshold           = 1
  treat_missing_data  = "breaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.collector.name
  }
}
