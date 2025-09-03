import { useEffect, useState } from 'react';

interface MenuButtonProps {
    setShowMenu: React.Dispatch<
        React.SetStateAction<
            | {
                  type: string;
                  show: boolean;
              }
            | undefined
        >
    >;
    onMenuClick?: () => void
    showMenu: { type: string; show: boolean } | undefined;
    typeKey: string;
}

const MenuButton = ({ setShowMenu, onMenuClick, showMenu, typeKey }: MenuButtonProps) => {
    const [isOpen, setIsOpen] = useState<boolean>();

    useEffect(() => {
        const toggle = showMenu?.type === typeKey && showMenu.show;
        setIsOpen(toggle);
    }, [showMenu, typeKey]);


    return (
        <div className="menu">
            <li
                onClick={() =>
                {
                    onMenuClick && onMenuClick()
                    setShowMenu({
                        type: typeKey,
                        show: showMenu?.type === typeKey ? !showMenu.show : true,
                    })
                }
                }
            >
                <details open={isOpen}>
                    <summary></summary>
                </details>
            </li>
        </div>
    );
};

export default MenuButton;
