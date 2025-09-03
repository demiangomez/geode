import { CheckIcon } from "@heroicons/react/24/outline";
import { FormReducerAction } from "@hooks/useFormReducer";

interface MenuContentProps {
    value: string;
    multiple?: boolean;
    multipleSelected?: any | undefined;
    alterValue?: any;
    typeKey: string;
    disabled?: boolean;
    alterFunction?: () => void;
    setMultipleSelected?: React.Dispatch<
        React.SetStateAction<string[] | undefined>
    >;
    dispatch?: (value: FormReducerAction) => void;
    alterFunctionWithValue?: (value: any) => void;
    uniqueId?: string | number; // Nuevo prop para identificador único
    setShowMenu: React.Dispatch<
        React.SetStateAction<
            | {
                  type: string;
                  show: boolean;
              }
            | undefined
        >
    >;
}

const MenuContent = ({
    value,
    multiple,
    multipleSelected,
    alterValue,
    typeKey,
    disabled,
    dispatch,
    alterFunction,
    setMultipleSelected,
    setShowMenu,
    alterFunctionWithValue,
    uniqueId,
}: MenuContentProps) => {
    const handleClick = () => {
        const newValue = alterValue ? alterValue : value;

        if (alterFunctionWithValue) {
            alterFunctionWithValue(newValue);
            // Solo cerrar el menú si no es modo múltiple
            if (!multiple) {
                setShowMenu(undefined);
            }
            return;
        } else if (multiple) {
            setMultipleSelected &&
                setMultipleSelected((prev) => {
                    if (prev) {
                        if (prev.includes(value)) {
                            return prev.filter((v) => v !== value);
                        } else {
                            return [...prev, value];
                        }
                    } else {
                        return [value];
                    }
                });
        } else {
            dispatch &&
                !setMultipleSelected &&
                dispatch({
                    type: "change_value",
                    payload: {
                        inputName: typeKey,
                        inputValue: newValue,
                    },
                });
            !multiple && setShowMenu(undefined);
        }
    };

    return (
        <li
            className={`py-2 px-4 font-semibold text-lg w-full ${disabled ? "disabled" : ""}`}
        >
            <button
                className={`w-full flex flex-wrap items-start justify-between px-2 text-left ${disabled ? "btn-disabled" : ""}`}
                type="button"
                onClick={() => {
                    dispatch ? handleClick() : alterFunction && alterFunction();
                }}
            >
                <span className="flex-1 mr-2 truncate">{value}</span>
                {multiple &&
                    uniqueId &&
                    Array.isArray(multipleSelected) &&
                    multipleSelected.includes(uniqueId) && (
                        <CheckIcon className="size-6 flex-shrink-0" />
                    )}
                {multiple && !uniqueId && multipleSelected.includes(value) && (
                    <CheckIcon className="size-6 flex-shrink-0" />
                )}
            </button>
        </li>
    );
};

export default MenuContent;
