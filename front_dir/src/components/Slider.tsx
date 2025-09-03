type Props = {
    name?: string;
    tittle?: string;
    disabled?: boolean;
    suffixValue?: string;
    suffixStyles?: object;
    classContainer?: string;
    classInput?: string;
    value: number;
    minValue: number;
    maxValue: number;
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
};

const Slider = ({
    name,
    tittle,
    disabled,
    suffixValue,
    suffixStyles,
    classContainer,
    classInput,
    value,
    minValue,
    maxValue,
    onChange,
}: Props) => {
    const valueToShow =
        value > maxValue ? maxValue : value < minValue ? minValue : value;

    return (
        <div className={classContainer}>
            {tittle && <span className="font-bold p-2 w-fit">{tittle}</span>}
            <div className="flex gap-1 p-2 w-full col-span-2 items-center">
                <input
                    type="range"
                    min={minValue}
                    max={maxValue}
                    name={name ?? ""}
                    value={valueToShow}
                    onChange={(e) => onChange(e)}
                    className={`range ${classInput ?? "range - secondary"}`}
                    disabled={disabled}
                />
                {suffixValue && (
                    <span style={suffixStyles} className="text-right font-bold">
                        {value + " " + suffixValue}
                    </span>
                )}
            </div>
        </div>
    );
};

export default Slider;
