#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

"""This modules groups functions made to run tests for a specific connector given a test context."""


from typing import Optional, Tuple

from ci_connector_ops.pipelines.actions import environments
from ci_connector_ops.pipelines.contexts import ConnectorTestContext
from ci_connector_ops.pipelines.models import Step, StepResult, StepStatus
from ci_connector_ops.pipelines.utils import check_path_in_workdir, with_exit_code, with_stderr, with_stdout
from dagger import Container, Directory

RUN_BLACK_CMD = ["python", "-m", "black", f"--config=/{environments.PYPROJECT_TOML_FILE_PATH}", "--check", "."]
RUN_ISORT_CMD = ["python", "-m", "isort", f"--settings-file=/{environments.PYPROJECT_TOML_FILE_PATH}", "--check-only", "--diff", "."]
RUN_FLAKE_CMD = ["python", "-m", "pflake8", f"--config=/{environments.PYPROJECT_TOML_FILE_PATH}", "."]


async def _run_tests_in_directory(connector_under_test: Container, test_directory: str) -> Tuple[StepStatus, Optional[str], Optional[str]]:
    """Runs the pytest tests in the test_directory that was passed.
    A StepStatus.SKIPPED is returned if no tests were discovered.
    Args:
        connector_under_test (Container): The connector under test container.
        test_directory (str): The directory in which the python test modules are declared

    Returns:
        Tuple[StepStatus, Optional[str], Optional[str]]: Tuple of StepStatus, stderr and stdout.
    """
    test_config = (
        "pytest.ini" if await check_path_in_workdir(connector_under_test, "pytest.ini") else "/" + environments.PYPROJECT_TOML_FILE_PATH
    )
    if await check_path_in_workdir(connector_under_test, test_directory):
        tester = connector_under_test.with_exec(
            [
                "python",
                "-m",
                "pytest",
                "-s",
                test_directory,
                "-c",
                test_config,
            ]
        )
        return StepStatus.from_exit_code(await with_exit_code(tester)), await with_stderr(tester), await with_stdout(tester)

    else:
        return StepStatus.SKIPPED, None, None


async def code_format_checks(connector_under_test: Container, step=Step.CODE_FORMAT_CHECKS) -> StepResult:
    """Run a code format check on the container source code.
    We call black, isort and flake commands:
    - Black formats the code: fails if the code is not formatted.
    - Isort checks the import orders: fails if the import are not properly ordered.
    - Flake enforces style-guides: fails if the style-guide is not followed.
    Args:
        connector_under_test (Container): The connector under test container.
        step (Step): The step in which the code format checks are run. Defaults to Step.CODE_FORMAT_CHECKS
    Returns:
        StepResult: Failure or success of the code format checks with stdout and stdout.
    """
    connector_under_test = step.get_dagger_pipeline(connector_under_test)

    formatter = (
        connector_under_test.with_exec(["echo", "Running black"])
        .with_exec(RUN_BLACK_CMD)
        .with_exec(["echo", "Running Isort"])
        .with_exec(RUN_ISORT_CMD)
        .with_exec(["echo", "Running Flake"])
        .with_exec(RUN_FLAKE_CMD)
    )
    return StepResult(
        step,
        StepStatus.from_exit_code(await with_exit_code(formatter)),
        stderr=await with_stderr(formatter),
        stdout=await with_stdout(formatter),
    )


async def run_unit_tests(connector_under_test: Container, step=Step.UNIT_TESTS) -> StepStatus:
    """Run all pytest tests declared in the unit_tests directory of the connector code.

    Args:
        connector_under_test (Container): The connector under test container.
        step (Step): The step in which the unit tests are run. Defaults to Step.UNIT_TESTS

    Returns:
        StepResult: Failure or success of the unit tests with stdout and stdout.
    """
    connector_under_test = step.get_dagger_pipeline(connector_under_test)
    step_status, stderr, stdout = await _run_tests_in_directory(connector_under_test, "unit_tests")
    return StepResult(
        step,
        step_status,
        stderr=stderr,
        stdout=stdout,
    )


async def run_integration_tests(connector_under_test: Container, step=Step.INTEGRATION_TESTS) -> StepStatus:
    """Run all pytest tests declared in the unit_tests directory of the connector code.

    Args:
        connector_under_test (Container): The connector under test container.
        step (Step): The step in which the integration tests are run. Defaults to Step.UNIT_TESTS

    Returns:
        StepResult: Failure or success of the integration tests with stdout and stdout.
    """
    connector_under_test = step.get_dagger_pipeline(connector_under_test)
    step_status, stderr, stdout = await _run_tests_in_directory(connector_under_test, "integration_tests")
    return StepResult(
        step,
        step_status,
        stderr=stderr,
        stdout=stdout,
    )


async def run_acceptance_tests(
    context: ConnectorTestContext,
    connector_under_test_image_id: str,
    step=Step.ACCEPTANCE_TESTS,
) -> Tuple[StepResult, Directory]:
    """Runs the acceptance test suite on a connector dev image.
    It's rebuilding the connector acceptance test image if the tag is :dev.

    Args:
        context (ConnectorTestContext): The current test context, providing a connector object, a dagger client, a repository directory and the secrets directory.
        connector_under_test_image_id (str): Connector under test image id, used as a cachebuster.
        step (Step): The step in which the acceptance tests are run. Defaults to Step.ACCEPTANCE_TESTS

    Returns:
        Tuple[StepResult, Directory]: Failure or success of the acceptances tests with stdout and stdout AND an updated secrets directory.

    """
    if not context.connector.acceptance_test_config:
        return StepResult(Step.ACCEPTANCE_TESTS, StepStatus.SKIPPED), None

    dagger_client = step.get_dagger_pipeline(context.dagger_client)

    docker_host_socket = dagger_client.host().unix_socket("/var/run/docker.sock")

    if context.connector_acceptance_test_image.endswith(":dev"):
        cat_container = context.connector_acceptance_test_source_dir.docker_build()
    else:
        cat_container = dagger_client.container().from_(context.connector_acceptance_test_image)

    cat_container = (
        cat_container.with_unix_socket("/var/run/docker.sock", docker_host_socket)
        .with_workdir("/test_input")
        .with_env_variable("CACHEBUSTER", connector_under_test_image_id)
        .with_mounted_directory("/test_input", context.get_connector_dir(exclude=["secrets", ".venv"]))
        .with_directory("/test_input/secrets", context.secrets_dir)
        .with_entrypoint(["python", "-m", "pytest", "-p", "connector_acceptance_test.plugin", "-r", "fEsx"])
        .with_exec(["--acceptance-test-config", "/test_input"])
    )

    secret_dir = cat_container.directory("/test_input/secrets")
    updated_secrets_dir = None
    if secret_files := await secret_dir.entries():
        for file_path in secret_files:
            if file_path.startswith("updated_configurations"):
                updated_secrets_dir = secret_dir
                break

    return (
        StepResult(
            step,
            StepStatus.from_exit_code(await with_exit_code(cat_container)),
            stderr=await with_stderr(cat_container),
            stdout=await with_stdout(cat_container),
        ),
        updated_secrets_dir,
    )


async def run_qa_checks(context: ConnectorTestContext, step=Step.QA_CHECKS) -> StepResult:
    """Runs our QA checks on a connector.

    Args:
        context (ConnectorTestContext): The current test context, providing a connector object, a dagger client and a repository directory.

    Returns:
        StepResult: Failure or success of the QA checks with stdout and stdout.
    """
    ci_connector_ops = await environments.with_ci_connector_ops(context)
    ci_connector_ops = step.get_dagger_pipeline(ci_connector_ops)
    filtered_repo = context.get_repo_dir(
        include=[
            str(context.connector.code_directory),
            str(context.connector.documentation_file_path),
            str(context.connector.icon_path),
            ".git",  # This is a big directory but ci_connectors_ops needs it...
            "airbyte-config/init/src/main/resources/seed/source_definitions.yaml",
            "airbyte-config/init/src/main/resources/seed/destination_definitions.yaml",
        ],
    )
    qa_checks = (
        ci_connector_ops.with_mounted_directory("/airbyte", filtered_repo)
        .with_workdir("/airbyte")
        .with_exec(["run-qa-checks", f"connectors/{context.connector.technical_name}"])
    )

    return StepResult(
        step,
        StepStatus.from_exit_code(await with_exit_code(qa_checks)),
        stderr=await with_stderr(qa_checks),
        stdout=await with_stdout(qa_checks),
    )
