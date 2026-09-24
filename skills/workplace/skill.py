from services.apps.workplace import CONFIGURE_WORKPLACE, WORK_MODE, WORKPLACE, TASK_STATUS, TASK_RETRY


def can_handle(command, services):
    return bool(WORKPLACE.fullmatch(command) or CONFIGURE_WORKPLACE.fullmatch(command) or WORK_MODE.fullmatch(command)
                or TASK_STATUS.fullmatch(command) or TASK_RETRY.fullmatch(command))


async def handle(command, context, services):
    if TASK_STATUS.fullmatch(command) or TASK_RETRY.fullmatch(command):
        return await services["workplace"].followup(command, context)
    return await services["workplace"].run(context.raw_text, context)
